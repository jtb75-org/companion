"""App API — Conversation (D.D.) routes."""

import base64
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.auth.dependencies import User, require_complete_profile
from app.conversation.llm import GeminiClient, get_llm_client
from app.conversation.prompt_builder import build_system_prompt
from app.conversation.state_manager import state_manager
from app.conversation.stt import transcribe_audio
from app.conversation.tool_executor import execute_tool
from app.conversation.tools import get_dd_tools
from app.conversation.tts import synthesize_speech
from app.db import get_db
from app.db.context import set_user_context
from app.models.system_config import SystemConfig
from app.schemas.conversation import (
    ConversationMessageRequest,
    ConversationStartRequest,
)
from app.services.field_crypto import encrypt_for_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversation", tags=["Conversation"])

DEFAULT_CONTEXT_WINDOW = 20


async def _get_context_window(db: AsyncSession) -> int:
    """Get context window size from system_config."""
    try:
        result = await db.execute(
            select(SystemConfig).where(
                SystemConfig.category == "dd_persona",
                SystemConfig.key == "context_window",
                SystemConfig.is_active.is_(True),
            )
        )
        config = result.scalar_one_or_none()
        if config and config.value:
            return int(config.value.get(
                "max_messages", DEFAULT_CONTEXT_WINDOW
            ))
    except Exception:
        pass
    return DEFAULT_CONTEXT_WINDOW


MAX_TOOL_ITERATIONS = 5


async def _generate_with_tools(
    llm,
    system_prompt: str,
    llm_messages: list[dict],
    db,
    user_id,
) -> str:
    """Generate a response, executing tool calls.

    Falls back to plain generate for non-Gemini clients.
    """
    if not isinstance(llm, GeminiClient):
        return await llm.generate(
            system_prompt, llm_messages, max_tokens=1024
        )

    from vertexai.generative_models import Content, Part

    # Convert dict messages to Content objects
    contents = []
    for msg in llm_messages:
        role = (
            "user" if msg["role"] == "user" else "model"
        )
        contents.append(
            Content(
                role=role,
                parts=[Part.from_text(msg["content"])],
            )
        )

    tools = get_dd_tools()

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await llm.generate_with_tools(
            system_prompt,
            contents,
            tools=tools,
            max_tokens=2048,
        )
        if response is None:
            return llm._fallback_response(llm_messages)

        candidate = response.candidates[0]
        parts = candidate.content.parts

        # Check for function calls
        fn_call = None
        for part in parts:
            if part.function_call:
                fn_call = part.function_call
                break

        if fn_call is None:
            # No tool call — return text (with safety check)
            from app.conversation.safety import (
                check_response_safety,
            )

            return check_response_safety(
                response.text, str(user_id)
            )

        # Execute the tool
        fn_name = fn_call.name
        fn_args = dict(fn_call.args) if fn_call.args else {}
        logger.info(
            "TOOL_CALL: %s arg_keys=%s user=%s",
            fn_name,
            list(fn_args.keys()),
            user_id,
        )
        result = await execute_tool(
            fn_name, fn_args, db, user_id
        )

        # Append model response + function result
        contents.append(candidate.content)
        contents.append(
            Content(
                parts=[
                    Part.from_function_response(
                        name=fn_name,
                        response={"result": result},
                    )
                ]
            )
        )

    # Exhausted iterations — return whatever we have (with safety check)
    from app.conversation.safety import check_response_safety

    return check_response_safety(response.text, str(user_id))


@router.post("/start", status_code=status.HTTP_201_CREATED)
async def start_conversation(
    data: ConversationStartRequest | None = None,
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Start a D.D. conversation session."""
    session = await state_manager.create_session(str(user.id))

    # Generate D.D.'s greeting
    trigger = "user_initiated"
    if data and data.initial_context:
        trigger = data.initial_context

    system_prompt = await build_system_prompt(db, user, trigger)
    llm = get_llm_client()

    name = user.nickname or user.preferred_name

    # Document review triggers: static intro + tool call to
    # get real document data (avoids hallucination)
    if trigger in ("document_review", "document_arrived"):
        intro = (
            f"Hi {name}. I have some mail for you. "
            "Let me pull it up."
        )
        session.add_message("assistant", intro)
        # Simulate a user prompt to trigger tool call
        session.add_message(
            "user", "[Show me the document]"
        )
        llm_messages = [
            {"role": m.role, "content": m.content}
            for m in session.messages
        ]
        followup = await _generate_with_tools(
            llm, system_prompt, llm_messages,
            db, user.id,
        )
        session.add_message("assistant", followup)
        greeting = f"{intro}\n\n{followup}"
    else:
        greeting_messages = [
            {
                "role": "user",
                "content": f"[Session started by {name}]",
            }
        ]
        from app.conversation.safety import (
            check_response_safety,
        )

        greeting = await llm.generate(
            system_prompt, greeting_messages, max_tokens=1024
        )
        greeting = check_response_safety(
            greeting, str(user.id)
        )
        session.add_message("assistant", greeting)
    await state_manager.update_session(session)

    # Persist chat session to DB for audit
    from app.models.chat_session import (
        ChatMessage,
        ChatSession,
    )

    try:
        from datetime import datetime, timezone

        db_session = ChatSession(
            user_id=user.id,
            session_id=session.session_id,
            message_count=1,
            started_at=datetime.now(timezone.utc),
        )
        db.add(db_session)
        await db.flush()
        db.add(
            ChatMessage(
                chat_session_id=db_session.id,
                user_id=db_session.user_id,
                role="assistant",
                # PHI at rest: encrypt transcript content under the member's
                # per-user envelope DEK (same scheme as RAG chunk_text / OCR
                # text / extracted_fields). /start commits once with the tenant
                # GUC still live (set by require_complete_profile), so the DEK
                # get-or-create and INSERT run inside RLS.
                content=await encrypt_for_user(
                    db, db_session.user_id, greeting
                ),
            )
        )
        await db.commit()
        logger.info(
            "CHAT_PERSIST: Session %s saved for user %s",
            session.session_id,
            user.id
        )
    except Exception as e:
        logger.error("CHAT_PERSIST_FAIL: %s", str(e), exc_info=True)
        await db.rollback()

    # Synthesize TTS audio for greeting (optional)
    audio_b64 = None
    try:
        voice_id = getattr(user, "voice_id", None) or "warm"
        audio_bytes = await synthesize_speech(greeting, voice_id)
        if audio_bytes:
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    except Exception:
        logger.warning("TTS failed for greeting, returning text only")

    return {
        "session_id": session.session_id,
        "greeting": greeting,
        "audio_data": audio_b64,
        "status": "active",
        "started_at": session.started_at,
    }


@router.post("/message")
async def send_message(
    data: ConversationMessageRequest,
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Send a message to D.D. and get a response."""
    # Find active session
    session = await state_manager.get_active_session(str(user.id))
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="No active conversation. Start one first.",
        )

    # STT: if audio provided but no text, transcribe
    user_text = data.text
    if data.audio_data and not user_text.strip():
        try:
            audio_bytes = base64.b64decode(data.audio_data)
            transcript = await transcribe_audio(audio_bytes)
            if transcript:
                user_text = transcript
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Could not transcribe audio. Please try again.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("STT decoding failed")
            raise HTTPException(
                status_code=400,
                detail="Invalid audio data.",
            ) from exc

    # Add user message
    session.add_message("user", user_text)

    # Check conversation integrity
    from app.conversation.safety import (
        check_conversation_integrity,
    )

    integrity = check_conversation_integrity(
        user_text, str(user.id), session.session_id
    )
    if integrity["alerts"]:
        logger.info(
            "INTEGRITY: user=%s alerts=%s",
            user.id,
            integrity["alerts"],
        )

    # Build prompt with full context
    system_prompt = await build_system_prompt(
        db, user, user_query=user_text
    )

    # Check for exploitation indicators in user message
    from app.conversation.safety import (
        handle_exploitation_detection,
    )

    system_prompt = await handle_exploitation_detection(
        user_text, user.id, system_prompt, db
    )

    # Apply sliding window to limit context
    window = await _get_context_window(db)
    recent = session.messages[-window:]

    llm_messages = [
        {"role": m.role, "content": m.content}
        for m in recent
    ]

    # Generate response — with tool use for Gemini
    llm = get_llm_client()
    response_text = await _generate_with_tools(
        llm, system_prompt, llm_messages, db, user.id
    )

    # Commit tool executor changes (review confirmations, bill
    # creation, etc.) before chat persistence — so they survive
    # even if chat persistence fails.
    await db.commit()

    # The commit above ended the request transaction, which released the
    # transaction-local `app.current_user_id` GUC. Re-establish the tenant
    # context (same authenticated user) so the ChatSession lookup + message
    # INSERTs below run inside RLS instead of matching zero rows. This
    # set_config is the autobegin execute for the next transaction, so the
    # SELECT/INSERT/commit that follow all share it.
    await set_user_context(db, user.id)

    # Add assistant response to session
    session.add_message("assistant", response_text)
    await state_manager.update_session(session)

    # Persist messages to DB
    try:
        from sqlalchemy import select as sa_select

        from app.models.chat_session import (
            ChatMessage,
            ChatSession,
        )
        result = await db.execute(
            sa_select(ChatSession).where(
                ChatSession.session_id == session.session_id
            )
        )
        db_session = result.scalar_one_or_none()
        if db_session:
            # PHI at rest: encrypt each turn under the member's per-user DEK.
            # The tenant GUC was re-set above (set_user_context), so the DEK
            # get-or-create and INSERTs run inside RLS.
            db.add(ChatMessage(
                chat_session_id=db_session.id,
                user_id=db_session.user_id,
                role="user",
                content=await encrypt_for_user(
                    db, db_session.user_id, user_text
                ),
            ))
            db.add(ChatMessage(
                chat_session_id=db_session.id,
                user_id=db_session.user_id,
                role="assistant",
                content=await encrypt_for_user(
                    db, db_session.user_id, response_text
                ),
            ))
            db_session.message_count += 2
            await db.commit()
            logger.info(
                "CHAT_PERSIST: Session %s updated with new message",
                session.session_id
            )
        else:
            logger.warning(
                "CHAT_PERSIST_MISSING: No session record found for sid %s",
                session.session_id
            )
    except Exception as e:
        logger.error("Failed to persist chat: %s", str(e), exc_info=True)
        await db.rollback()

    # Synthesize TTS audio for response (optional)
    audio_b64 = None
    try:
        voice_id = getattr(user, "voice_id", None) or "warm"
        logger.info(
            "TTS: synthesizing for voice=%s text_len=%d",
            voice_id,
            len(response_text),
        )
        audio_bytes = await synthesize_speech(
            response_text, voice_id
        )
        if audio_bytes:
            audio_b64 = base64.b64encode(
                audio_bytes
            ).decode("utf-8")
            logger.info(
                "TTS: generated %d bytes of audio",
                len(audio_bytes),
            )
        else:
            logger.warning("TTS: returned no audio bytes")
    except Exception:
        logger.exception(
            "TTS failed for response, returning text only"
        )

    return {
        "session_id": session.session_id,
        "response": response_text,
        "audio_data": audio_b64,
        "message_count": len(session.messages),
    }


@router.post("/message/stream")
async def send_message_stream(
    data: ConversationMessageRequest,
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Stream a D.D. response via SSE."""
    session = await state_manager.get_active_session(
        str(user.id)
    )
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="No active conversation. Start one first.",
        )

    # Add user message before streaming
    session.add_message("user", data.text)
    await state_manager.update_session(session)

    system_prompt = await build_system_prompt(
        db, user, user_query=data.text
    )
    window = await _get_context_window(db)
    recent = session.messages[-window:]
    llm_messages = [
        {"role": m.role, "content": m.content}
        for m in recent
    ]
    llm = get_llm_client()

    async def event_generator():
        full_response = ""
        cut_short: dict | None = None
        try:
            async for token in llm.generate_stream(
                system_prompt, llm_messages, max_tokens=1024
            ):
                # A dict chunk is a terminal meta-event (e.g. a mid-answer cut),
                # NOT answer text — capture it for the done event, don't forward
                # it as a token or fold it into full_response.
                if isinstance(token, dict):
                    cut_short = token
                    continue
                full_response += token
                event = json.dumps({"token": token})
                yield f"data: {event}\n\n"

            # Save full assistant response after stream ends
            session.add_message("assistant", full_response)
            await state_manager.update_session(session)

            # Persist to DB
            try:
                from sqlalchemy import select as sa_sel

                from app.models.chat_session import (
                    ChatMessage,
                    ChatSession,
                )

                # The SSE generator runs after the request handler returned,
                # so the request transaction (and its transaction-local
                # `app.current_user_id` GUC) is gone. Re-establish the tenant
                # context for the same authenticated user before the RLS-
                # protected ChatSession lookup + message INSERTs, or RLS
                # matches zero rows and the turn is silently dropped.
                await set_user_context(db, user.id)

                res = await db.execute(
                    sa_sel(ChatSession).where(
                        ChatSession.session_id
                        == session.session_id
                    )
                )
                db_sess = res.scalar_one_or_none()
                if db_sess:
                    # PHI at rest: encrypt each turn under the member's per-user
                    # DEK. set_user_context above re-set the tenant GUC so the
                    # DEK get-or-create and INSERTs run inside RLS.
                    db.add(ChatMessage(
                        chat_session_id=db_sess.id,
                        user_id=db_sess.user_id,
                        role="user",
                        content=await encrypt_for_user(
                            db, db_sess.user_id, data.text
                        ),
                    ))
                    db.add(ChatMessage(
                        chat_session_id=db_sess.id,
                        user_id=db_sess.user_id,
                        role="assistant",
                        content=await encrypt_for_user(
                            db, db_sess.user_id, full_response
                        ),
                    ))
                    db_sess.message_count += 2
                    await db.commit()
                    logger.info(
                        "CHAT_PERSIST: Streamed session %s updated",
                        session.session_id
                    )
                else:
                    logger.warning(
                        "CHAT_PERSIST_MISSING: No streamed session for sid %s",
                        session.session_id
                    )
            except Exception as e:
                logger.error("Failed to persist streamed chat: %s", str(e), exc_info=True)
                await db.rollback()

            done_payload = {"done": True, "full_response": full_response}
            if cut_short:
                # Signal the client to render a soft "response stopped early"
                # note. reason is a coarse category ("content" | "length").
                done_payload["cut_short"] = True
                done_payload["cut_reason"] = cut_short.get("reason", "content")
            done_event = json.dumps(done_payload)
            yield f"data: {done_event}\n\n"
        except Exception:
            logger.exception("SSE stream error")
            if not full_response:
                full_response = (
                    "Sorry, something went wrong. "
                    "Please try again."
                )
                session.add_message(
                    "assistant", full_response
                )
                await state_manager.update_session(session)
            err = json.dumps(
                {"error": True, "full_response": full_response}
            )
            yield f"data: {err}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@router.get("/state")
async def conversation_state(
    user: User = Depends(require_complete_profile),
):
    """Get current conversation state."""
    session = await state_manager.get_active_session(str(user.id))
    if session is None:
        return {
            "session_id": None,
            "status": "inactive",
            "message_count": 0,
        }
    return {
        "session_id": session.session_id,
        "status": "active",
        "message_count": len(session.messages),
        "current_topic": session.current_topic,
        "started_at": session.started_at,
        "last_activity": session.last_activity,
    }


@router.post("/end")
async def end_conversation(
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """End the current D.D. session."""
    session = await state_manager.get_active_session(str(user.id))
    if session is None:
        return {"status": "no_active_session"}

    await state_manager.end_session(str(user.id), session.session_id)

    # Clean up integrity tracker
    from app.conversation.safety import reset_session_tracker

    reset_session_tracker(str(user.id), session.session_id)

    # Mark ended in DB
    from datetime import datetime as dt

    from sqlalchemy import select as s

    from app.models.chat_session import (
        ChatSession as CSe,
    )

    try:
        result = await db.execute(
            s(CSe).where(
                CSe.session_id == session.session_id
            )
        )
        db_session = result.scalar_one()
        db_session.ended_at = dt.utcnow()
        await db.commit()
    except Exception:
        logger.exception("Failed to mark session ended")
        await db.rollback()

    return {
        "status": "ended",
        "session_id": session.session_id,
        "message_count": len(session.messages),
    }
