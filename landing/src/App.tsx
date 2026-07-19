import { Header } from './components/Header';
import { Hero } from './components/Hero';
import { HowItWorks } from './components/HowItWorks';
import { ProductBridge } from './components/ProductBridge';
import { CTA } from './components/CTA';
import { Footer } from './components/Footer';

export function App() {
  return (
    <>
      <a className="skip-link" href="#tool">
        Skip to content
      </a>
      <Header />
      <main id="main">
        <Hero />
        <HowItWorks />
        <ProductBridge />
        <CTA />
      </main>
      <Footer />
    </>
  );
}
