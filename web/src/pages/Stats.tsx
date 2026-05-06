import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type Project } from "@/api/client";
import { Metric, Panel } from "@/components/ui";

type StatsPayload = {
  timeline: Array<{ date: string; quality: number | null; skipped: boolean }>;
  pose: Array<{ date: string; yaw: number | null; pitch: number | null; roll: number | null }>;
  eye_open: number[];
  photos_by_month: Array<{ month: string; count: number }>;
  total: number;
  skipped: number;
};

export function Stats({ project }: { project: Project }) {
  const statsQuery = useQuery({
    queryKey: ["stats", project.id],
    queryFn: () => api.stats(project.id) as Promise<StatsPayload>,
  });
  const stats = statsQuery.data;
  const avgQuality = stats?.timeline.length
    ? stats.timeline.reduce((sum, item) => sum + (item.quality ?? 0), 0) / stats.timeline.length
    : 0;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <Metric label="Cataloged" value={stats?.total ?? project.photo_count} />
        <Metric label="Skipped" value={stats?.skipped ?? project.skipped_count} tone={project.skipped_count ? "warn" : "default"} />
        <Metric label="Avg quality" value={avgQuality.toFixed(2)} tone={avgQuality >= 0.7 ? "good" : "default"} />
        <Metric label="Active ratio" value={project.photo_count ? `${Math.round((project.active_count / project.photo_count) * 100)}%` : "0%"} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartPanel title="Quality over time">
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={stats?.timeline ?? []}>
              <CartesianGrid stroke="#11141222" />
              <XAxis dataKey="date" tickFormatter={(value) => String(value).slice(0, 10)} minTickGap={36} />
              <YAxis domain={[0, 1]} />
              <Tooltip />
              <Line type="monotone" dataKey="quality" stroke="#1F7A75" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </ChartPanel>

        <ChartPanel title="Pose distribution">
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={stats?.pose ?? []}>
              <CartesianGrid stroke="#11141222" />
              <XAxis dataKey="date" tickFormatter={(value) => String(value).slice(0, 10)} minTickGap={36} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="yaw" stroke="#1F7A75" dot={false} />
              <Line type="monotone" dataKey="pitch" stroke="#C59A2D" dot={false} />
              <Line type="monotone" dataKey="roll" stroke="#C94F31" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </ChartPanel>

        <ChartPanel title="Photos by month">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={stats?.photos_by_month ?? []}>
              <CartesianGrid stroke="#11141222" />
              <XAxis dataKey="month" minTickGap={28} />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" fill="#202522" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartPanel>

        <ChartPanel title="Eye-open histogram">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={histogram(stats?.eye_open ?? [])}>
              <CartesianGrid stroke="#11141222" />
              <XAxis dataKey="bucket" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" fill="#C59A2D" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartPanel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel>
          <h3 className="font-black text-ink">Average face</h3>
          <img className="mt-3 aspect-video w-full rounded-md bg-ink object-contain" src={`/api/projects/${project.id}/avg-face`} alt="" />
        </Panel>
        <Panel>
          <h3 className="font-black text-ink">Landmark drift heatmap</h3>
          <img className="mt-3 aspect-video w-full rounded-md bg-ink object-contain" src={`/api/projects/${project.id}/heatmap`} alt="" />
        </Panel>
      </div>
    </div>
  );
}

function ChartPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Panel>
      <h3 className="mb-3 font-black text-ink">{title}</h3>
      {children}
    </Panel>
  );
}

function histogram(values: number[]) {
  const buckets = Array.from({ length: 8 }, (_, idx) => ({ bucket: `${(idx * 0.1).toFixed(1)}+`, count: 0 }));
  for (const value of values) {
    const idx = Math.max(0, Math.min(buckets.length - 1, Math.floor(value * 10)));
    buckets[idx].count += 1;
  }
  return buckets;
}
