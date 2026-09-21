import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { motion } from "framer-motion";
import { LiquidGlassProvider } from "./components/LiquidGlassProvider";
import { StatusBar, type ViewKey } from "./components/StatusBar";
import { Evolution } from "./views/Evolution";
import { Overview } from "./views/Overview";
import { PaperTrading } from "./views/PaperTrading";
import { RetiredVenue } from "./views/RetiredVenue";
import { VenueView } from "./views/VenueView";
import "./index.css";

function App() {
  const [view, setView] = useState<ViewKey>("overview");
  return (
    <div className="min-h-screen w-full flex flex-col">
      <LiquidGlassProvider />
      <StatusBar view={view} onView={setView} />
      <main className="flex-1 max-w-[1700px] w-full mx-auto">
        {/* No AnimatePresence, deliberately.
         *
         * `mode="wait"` defers the incoming view until the outgoing one has
         * finished exiting, and the three keys here cycle. Re-selecting a key
         * that is still exiting makes framer treat the ENTERING child as the
         * exiting one: it sticks at opacity 0, translateY(-8px), its unmount
         * cleanup never runs, and the page is blank from then on — reproducible
         * on the third switch, every time.
         *
         * The exit animation was 180ms of polish guarding nothing. Keeping the
         * entrance and dropping the exit removes the failure entirely, and the
         * view now unmounts synchronously, which is also what the theme hooks
         * want: restore runs before the next view paints. */}
        <motion.div key={view}
          initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18 }}>
          {view === "overview" && <Overview />}
          {view === "paper" && <PaperTrading />}
          {/* Retired, not deleted — the tab stays so the decision is visible. */}
          {view === "polymarket" && <RetiredVenue venue="polymarket_us" title="Polymarket" />}
          {view === "robinhood" && <VenueView venue="robinhood" />}
          {view === "evolution" && <Evolution />}
        </motion.div>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><App /></StrictMode>
);
