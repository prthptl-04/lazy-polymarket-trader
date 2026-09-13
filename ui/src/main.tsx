import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, motion } from "framer-motion";
import { LiquidGlassProvider } from "./components/LiquidGlassProvider";
import { StatusBar, type ViewKey } from "./components/StatusBar";
import { Overview } from "./views/Overview";
import { VenueView } from "./views/VenueView";
import "./index.css";

function App() {
  const [view, setView] = useState<ViewKey>("overview");
  return (
    <div className="min-h-screen w-full flex flex-col">
      <LiquidGlassProvider />
      <StatusBar view={view} onView={setView} />
      <main className="flex-1 max-w-[1700px] w-full mx-auto">
        <AnimatePresence mode="wait">
          <motion.div key={view}
            initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.18 }}>
            {view === "overview" && <Overview />}
            {view === "polymarket" && <VenueView venue="polymarket_us" />}
            {view === "robinhood" && <VenueView venue="robinhood" />}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><App /></StrictMode>
);
