import { useQuery } from "@tanstack/react-query";
import { BarChart3, FolderOpen, Images, Monitor, Sparkles, ScanFace, SlidersHorizontal } from "lucide-react";
import { api } from "@/api/client";
import { Badge, PageFrame, Panel } from "@/components/ui";
import { MoreLink } from "@/components/MobileLayout";

export type MoreTarget = "setup" | "render" | "review" | "stats" | "history" | "grid" | "auto-render-settings";

export function More({ onNavigate, onSwitchToDesktop }: { onNavigate: (target: MoreTarget) => void; onSwitchToDesktop: () => void }) {
  const todayQuery = useQuery({ queryKey: ["today"], queryFn: api.today });
  const today = todayQuery.data;

  return (
    <PageFrame size="narrow">
      <header>
        <h1 className="text-2xl font-black tracking-tight text-ink">More</h1>
        <p className="mt-1 text-sm font-semibold text-ink/55">Admin tools and detailed views.</p>
      </header>

      <div className="grid gap-2">
        <MoreLink
          icon={<Sparkles className="h-5 w-5" />}
          label="Auto-render"
          description="Schedule, default video size, smoothness."
          onClick={() => onNavigate("auto-render-settings")}
        />
        <MoreLink
          icon={<ScanFace className="h-5 w-5" />}
          label="Review"
          description={
            today?.project?.id
              ? `Frames flagged by quality checks${today?.project ? "" : ""}`
              : "Frames flagged by quality checks"
          }
          onClick={() => onNavigate("review")}
        />
        <MoreLink
          icon={<SlidersHorizontal className="h-5 w-5" />}
          label="Custom render"
          description="One-off video with your own size, range, and music."
          onClick={() => onNavigate("render")}
        />
        <MoreLink
          icon={<Images className="h-5 w-5" />}
          label="All photos"
          description="Browse the catalog and switch include/exclude per photo."
          onClick={() => onNavigate("grid")}
        />
        <MoreLink
          icon={<BarChart3 className="h-5 w-5" />}
          label="Stats"
          description="Capture cadence, quality, and pose over time."
          onClick={() => onNavigate("stats")}
        />
        <MoreLink
          icon={<FolderOpen className="h-5 w-5" />}
          label="Setup"
          description="Inbox folder, project, factory reset."
          onClick={() => onNavigate("setup")}
        />
      </div>

      <Panel className="border border-ink/10">
        <div className="flex items-center gap-2">
          <Monitor className="h-5 w-5 text-teal" />
          <h2 className="text-sm font-black uppercase tracking-[0.12em] text-ink">Desktop view</h2>
        </div>
        <p className="mt-1 text-xs font-semibold text-ink/55">
          The desktop layout shows everything at once. Best on a laptop.
        </p>
        <button
          type="button"
          className="mt-3 w-full rounded-md border border-ink/15 bg-paper px-3 py-2 text-sm font-black text-ink shadow-line hover:border-teal/40"
          onClick={onSwitchToDesktop}
        >
          Switch to desktop layout
        </button>
      </Panel>

      <Panel>
        <div className="text-xs font-bold uppercase tracking-[0.18em] text-ink/45">About</div>
        <div className="mt-2 text-sm font-semibold text-ink/65">
          SelfieTL · {todayQuery.data?.project?.source_folder ? <span className="break-all font-mono text-xs text-ink/45">{todayQuery.data.project.source_folder}</span> : null}
        </div>
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          {today?.canonical_ready ? <Badge tone="good">Anchor ready</Badge> : <Badge tone="warn">Anchor not ready</Badge>}
          {today?.project ? <Badge>{today.project.active_count}/{today.project.photo_count} included</Badge> : null}
        </div>
      </Panel>
    </PageFrame>
  );
}
