import { useState } from "react";
import { CalendarDays, ScanFace } from "lucide-react";
import { cn } from "@/components/ui";
import { FaceChange } from "@/pages/FaceChange";
import { Timeline } from "@/pages/Timeline";

export function Progress() {
  const [view, setView] = useState<"shape" | "calendar">("shape");
  return (
    <div>
      <div className="mx-auto mb-4 grid max-w-md grid-cols-2 rounded-lg border border-ink/10 bg-paper p-1 shadow-line">
        <button
          type="button"
          onClick={() => setView("shape")}
          className={cn("flex min-h-11 items-center justify-center gap-2 rounded-md text-sm font-black", view === "shape" ? "bg-ink text-paper" : "text-ink/55")}
        >
          <ScanFace className="h-4 w-4" />
          Shape
        </button>
        <button
          type="button"
          onClick={() => setView("calendar")}
          className={cn("flex min-h-11 items-center justify-center gap-2 rounded-md text-sm font-black", view === "calendar" ? "bg-ink text-paper" : "text-ink/55")}
        >
          <CalendarDays className="h-4 w-4" />
          Calendar
        </button>
      </div>
      {view === "shape" ? <FaceChange /> : <Timeline />}
    </div>
  );
}
