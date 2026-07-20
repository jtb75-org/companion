import { Header } from './components/Header';
import { Hero } from './components/Hero';
import { WhatItDoes } from './components/WhatItDoes';
import { BenefitsResource } from './components/BenefitsResource';
import { CTA } from './components/CTA';
import { Footer } from './components/Footer';

export function App() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Header />
      <main id="main">
        <Hero />
        <WhatItDoes />
        <BenefitsResource />
        <CTA />
      </main>
      <Footer />
    </>
  );
}
