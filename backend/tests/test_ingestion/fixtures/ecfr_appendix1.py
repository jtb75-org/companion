"""Representative fixture for the Blue Book appendix (20 CFR Pt. 404 Subpt. P App. 1).

Hand-built to mirror the LIVE eCFR enhanced-renderer markup observed at
``GET /api/renderer/v1/content/enhanced/current/title-20?chapter=III&part=404
&appendix=Appendix 1 to Subpart P of Part 404``:

  * the whole appendix is one ``<div class="appendix">``;
  * a leading provenance banner (``box``/``seal-block``) that MUST be ignored;
  * a table-of-contents run of bare ``N.00`` rows (no body) that must NOT become
    narrative docs;
  * ``Part A`` (adults) then ``Part B`` (children);
  * body-system headers ``N.00`` (``hd1-paragraph``) + a narrative section;
  * a ``N.01 Category of Impairments`` divider;
  * individual listings as unclassed ``<p>NN.NN <em>title</em> ...:</p>`` with
    lettered/numbered criteria paragraphs, including a ``[Reserved]`` slot and a
    mid-criteria formula-looking number (``9.57 ×``) that must NOT be mistaken
    for a listing;
  * a trailing editorial note that must be ignored.

Trimmed to two adult body systems (1.00, 12.00) + one childhood system (112.00)
so the expected doc set is small and assertable, while exercising every branch
of the segmenter.
"""

APPENDIX1_HTML = """
<div class="appendix">
  <div class="box box-published no-footer">
    <p>Enhanced content is provided to the user to provide additional context.</p>
  </div>
  <div class="seal-block seal-block-header"><p>Published Edition</p></div>
  <h4>Appendix 1 to Subpart P of Part 404—Listing of Impairments</h4>

  <p class="flush-paragraph-2">1.00 Musculoskeletal Disorders</p>
  <p class="flush-paragraph-2">12.00 Mental Disorders.</p>

  <p class="hd2-paragraph">Part A</p>

  <p class="hd1-paragraph">1.00 Musculoskeletal Disorders</p>
  <p>A. Which disorders do we evaluate under these listings? We evaluate
     musculoskeletal disorders that result in functional limitations.</p>
  <p>B. What evidence do we need? We need objective medical evidence establishing
     the disorder, including the required imaging described in 1.00C.</p>
  <p class="hd1-paragraph">1.01 Category of Impairments, Musculoskeletal Disorders</p>
  <p>1.15 <em>Disorders of the skeletal spine resulting in compromise of a nerve
     root(s)</em> (see 1.00F), documented by A, B, C, and D:</p>
  <p>A. Neuro-anatomic distribution of pain.</p>
  <p>B. Radicular distribution of neurological signs.</p>
  <p>C. Findings on imaging consistent with compromise of a nerve root.</p>
  <p>D. Impairment-related physical limitation of musculoskeletal functioning.</p>
  <p>1.16 <em>Lumbar spinal stenosis resulting in compromise of the cauda
     equina</em> (see 1.00G), documented by A, B, C, and D:</p>
  <p>A. Symptoms of neurological compromise.</p>
  <p>B. Nonradicular distribution of pain in one or both lower extremities.</p>

  <p class="hd1-paragraph">12.00 Mental Disorders</p>
  <p>A. How are the listings for mental disorders arranged, and what do they
     require? The listings are arranged in 11 categories. Each has paragraph A
     criteria and functional (paragraph B) criteria.</p>
  <p>B. Which mental disorders do we evaluate, and how do we define them? We
     evaluate depressive, bipolar, anxiety, and other disorders described here.</p>
  <p class="hd1-paragraph">12.01 Category of Impairments, Mental Disorders</p>
  <p>12.02 <em>Neurocognitive disorders</em> (see 12.00B1), satisfied by A and B,
     or A and C:</p>
  <p>A. Medical documentation of a significant cognitive decline.</p>
  <p>B. Extreme limitation of one, or marked limitation of two, areas of mental
     functioning.</p>
  <p>12.03 [Reserved]</p>
  <p>12.04 <em>Depressive, bipolar and related disorders</em> (see 12.00B3),
     satisfied by A and B, or A and C:</p>
  <p>A. Medical documentation of the requirements of paragraph 1 or 2:</p>
  <p>1. Depressive disorder, characterized by five or more of the following:</p>
  <p>a. Depressed mood;</p>
  <p>b. Diminished interest in almost all activities;</p>
  <p>2. Bipolar disorder, characterized by three or more of the following.</p>
  <p>B. Extreme limitation of one, or marked limitation of two, of the following
     areas of mental functioning (a formula such as 9.57 × [loge(x)] is not a
     listing header and must be kept within this listing's body):</p>
  <p>1. Understand, remember, or apply information.</p>
  <p>2. Interact with others.</p>

  <p class="hd2-paragraph">Part B</p>
  <p class="hd1-paragraph">112.00 Mental Disorders</p>
  <p>A. How are the listings for mental disorders in children arranged? They are
     arranged in the same categories as the adult listings, with age-appropriate
     functional criteria.</p>
  <p>B. Which mental disorders do we evaluate in children? We evaluate the same
     categories described for adults, adjusted for childhood development.</p>
  <p class="hd1-paragraph">112.01 Category of Impairments, Mental Disorders</p>
  <p>112.04 <em>Depressive, bipolar and related disorders</em> (see 112.00B3),
     for children age 3 to attainment of age 18, satisfied by A and B:</p>
  <p>A. Medical documentation of the requirements of paragraph 1 or 2.</p>
  <p>B. Extreme limitation of one, or marked limitation of two, areas of
     age-appropriate functioning.</p>

  <div class="editorial-note">
    <h6>Editorial Note</h6>
    <p>This appendix was last revised at 89 FR 12345.</p>
  </div>
</div>
"""
