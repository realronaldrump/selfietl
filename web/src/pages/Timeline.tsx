import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Eye,
  Save,
  Trash2,
  XCircle,
} from "lucide-react";
import { api, type CalendarDay, type CapturedPhoto } from "@/api/client";
import { Badge, Button, PageFrame, Panel, cn } from "@/components/ui";

export function Timeline() {
  const today = useMemo(() => new Date(), []);
  const [cursor, setCursor] = useState<{ year: number; month: number }>({
    year: today.getFullYear(),
    month: today.getMonth(),
  });
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const range = useMemo(() => monthRange(cursor.year, cursor.month), [cursor]);
  const calendarQuery = useQuery({
    queryKey: ["calendar", range.start, range.end],
    queryFn: () => api.calendar({ start: range.start, end: range.end }),
  });
  const days = calendarQuery.data?.days ?? [];
  const dayMap = useMemo(() => new Map(days.map((d) => [d.date, d])), [days]);

  return (
    <PageFrame size="narrow">
      <header>
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-black tracking-tight text-ink">Timeline</h1>
          <CalendarDays className="h-5 w-5 text-teal" />
        </div>
        <p className="mt-1 text-sm font-semibold text-ink/55">Each filled day is a selfie. Tap to see it.</p>
      </header>

      <Panel>
        <div className="flex items-center justify-between">
          <Button size="sm" variant="ghost" onClick={() => setCursor(shiftMonth(cursor, -1))}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <div className="text-base font-black text-ink">{monthLabel(cursor.year, cursor.month)}</div>
          <Button size="sm" variant="ghost" onClick={() => setCursor(shiftMonth(cursor, 1))}>
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>

        <CalendarGrid
          year={cursor.year}
          month={cursor.month}
          dayMap={dayMap}
          today={today}
          onSelect={(date) => setSelectedDay(date)}
        />
        <div className="mt-3 grid grid-cols-3 gap-2 text-[0.62rem] font-black uppercase tracking-[0.16em] text-ink/55">
          <Legend tone="good" label="Locked in" />
          <Legend tone="warn" label="Captured but flagged" />
          <Legend tone="default" label="No selfie" />
        </div>
      </Panel>

      {selectedDay ? (
        <DaySheet day={selectedDay} onClose={() => setSelectedDay(null)} onDateChanged={setSelectedDay} />
      ) : (
        <RecentList
          days={days}
          onSelect={(date) => setSelectedDay(date)}
          loading={calendarQuery.isLoading}
        />
      )}
    </PageFrame>
  );
}

function CalendarGrid({
  year,
  month,
  dayMap,
  today,
  onSelect,
}: {
  year: number;
  month: number;
  dayMap: Map<string, CalendarDay>;
  today: Date;
  onSelect: (date: string) => void;
}) {
  const cells = useMemo(() => buildMonthCells(year, month), [year, month]);
  const todayIso = isoDate(today);
  return (
    <div className="mt-4">
      <div className="grid grid-cols-7 gap-1 text-center text-[0.62rem] font-black uppercase tracking-[0.16em] text-ink/45">
        {["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map((label) => (
          <div key={label}>{label}</div>
        ))}
      </div>
      <div className="mt-2 grid grid-cols-7 gap-1">
        {cells.map((cell, idx) => {
          if (!cell.date) {
            return <div key={`pad-${idx}`} className="aspect-square" />;
          }
          const day = dayMap.get(cell.date);
          const isToday = cell.date === todayIso;
          const isFuture = cell.date > todayIso;
          const tone = day?.has_active ? "good" : day ? "warn" : "default";
          return (
            <button
              key={cell.date}
              type="button"
              disabled={!day && isFuture}
              onClick={() => day && onSelect(cell.date!)}
              className={cn(
                "relative aspect-square overflow-hidden rounded-md border text-xs font-black",
                isToday ? "border-coral" : "border-ink/10",
                day && tone === "good" ? "bg-ink text-paper" : "",
                day && tone === "warn" ? "bg-coral/15 text-coral" : "",
                !day ? "bg-paper text-ink/55" : "",
                !day && isFuture ? "opacity-35" : "",
              )}
            >
              {day?.thumb_url ? (
                <img src={day.thumb_url} alt="" className="absolute inset-0 h-full w-full object-cover opacity-90" />
              ) : null}
              <span
                className={cn(
                  "absolute right-1 top-1 text-[0.62rem]",
                  day?.thumb_url ? "rounded bg-ink/70 px-1 text-paper" : "",
                )}
              >
                {Number(cell.date.slice(8))}
              </span>
              {day?.count && day.count > 1 ? (
                <span className="absolute left-1 bottom-1 rounded bg-paper/85 px-1 text-[0.55rem] text-ink">×{day.count}</span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function RecentList({
  days,
  onSelect,
  loading,
}: {
  days: CalendarDay[];
  onSelect: (date: string) => void;
  loading: boolean;
}) {
  const sorted = [...days].sort((a, b) => (a.date < b.date ? 1 : -1)).slice(0, 8);
  if (loading) return <Panel className="text-sm font-semibold text-ink/55">Loading timeline…</Panel>;
  if (sorted.length === 0) return <Panel className="text-sm font-semibold text-ink/55">No selfies yet this month.</Panel>;
  return (
    <Panel>
      <h2 className="text-sm font-black uppercase tracking-[0.12em] text-ink">Recent</h2>
      <div className="mt-3 space-y-2">
        {sorted.map((day) => (
          <button
            type="button"
            key={day.date}
            onClick={() => onSelect(day.date)}
            className="flex w-full items-center gap-3 rounded-md border border-ink/10 bg-paper p-2 text-left transition hover:border-teal/30"
          >
            <div className="h-12 w-12 shrink-0 overflow-hidden rounded-md bg-ink">
              {day.thumb_url ? (
                <img src={day.thumb_url} alt="" className="h-full w-full object-cover" />
              ) : (
                <div className="grid h-full w-full place-items-center text-paper/55 text-xs">—</div>
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-black text-ink">{formatLongDate(day.date)}</div>
              <div className="text-xs font-semibold text-ink/55">
                {day.count > 1 ? `${day.count} captures · ` : ""}
                {day.has_active ? "locked in" : "needs review"}
              </div>
            </div>
            {day.has_active ? <Badge tone="good">included</Badge> : <Badge tone="warn">review</Badge>}
          </button>
        ))}
      </div>
    </Panel>
  );
}

function DaySheet({ day, onClose, onDateChanged }: { day: string; onClose: () => void; onDateChanged: (day: string) => void }) {
  const queryClient = useQueryClient();
  const [selectedHash, setSelectedHash] = useState<string | null>(null);
  const dayQuery = useQuery({
    queryKey: ["day", day],
    queryFn: () => api.photosByDate(day),
  });
  const photos = dayQuery.data?.photos ?? [];
  const primaryPhoto = primaryPhotoForDay(photos);
  const photo = photos.find((item) => item.hash === selectedHash) ?? primaryPhoto;

  useEffect(() => {
    if (photos.length === 0) {
      setSelectedHash(null);
      return;
    }
    if (!selectedHash || !photos.some((item) => item.hash === selectedHash)) {
      setSelectedHash(primaryPhoto?.hash ?? photos[photos.length - 1].hash);
    }
  }, [photos, primaryPhoto, selectedHash]);

  const includeMutation = useMutation({
    mutationFn: ({ hash, skipped }: { hash: string; skipped: boolean }) =>
      api.patchPhoto(hash, { skipped, user_override: !skipped }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["day", day] });
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      queryClient.invalidateQueries({ queryKey: ["today"] });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (hash: string) => api.deleteCapture(hash),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["day", day] });
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      queryClient.invalidateQueries({ queryKey: ["today"] });
    },
  });
  const dateMutation = useMutation({
    mutationFn: ({ hash, capturedAt }: { hash: string; capturedAt: string }) =>
      api.patchPhoto(hash, { captured_at: capturedAt }),
    onSuccess: (updated) => {
      const nextDay = updated.captured_at.slice(0, 10);
      queryClient.invalidateQueries({ queryKey: ["day", day] });
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      queryClient.invalidateQueries({ queryKey: ["today"] });
      if (nextDay && nextDay !== day) {
        queryClient.invalidateQueries({ queryKey: ["day", nextDay] });
        onDateChanged(nextDay);
      }
    },
  });

  return (
    <Panel className="overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-ink/10 px-4 py-3">
        <div>
          <div className="text-xs font-black uppercase tracking-[0.16em] text-ink/55">Day</div>
          <div className="text-base font-black text-ink">{formatLongDate(day)}</div>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          <XCircle className="h-4 w-4" />
          Close
        </Button>
      </div>
      <div className="p-4">
        {dayQuery.isLoading ? (
          <div className="text-sm font-semibold text-ink/55">Loading…</div>
        ) : !photo ? (
          <div className="text-sm font-semibold text-ink/55">No selfies cataloged for this day.</div>
        ) : (
          <DayPhotoView
            photo={photo}
            onTogglePending={includeMutation.isPending}
            onToggle={() => includeMutation.mutate({ hash: photo.hash, skipped: !photo.skipped })}
            onDelete={() => deleteMutation.mutate(photo.hash)}
            deletePending={deleteMutation.isPending}
            onDateSave={(capturedAt) => dateMutation.mutate({ hash: photo.hash, capturedAt })}
            datePending={dateMutation.isPending}
          />
        )}
        {photos.length > 1 ? (
          <div className="mt-4">
            <h3 className="text-xs font-black uppercase tracking-[0.16em] text-ink/55">Captures for this day</h3>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {photos.map((capture) => (
                <button
                  key={capture.hash}
                  type="button"
                  onClick={() => setSelectedHash(capture.hash)}
                  className={cn(
                    "group block overflow-hidden rounded-md border bg-ink text-left",
                    capture.hash === photo?.hash ? "border-teal ring-2 ring-teal/25" : "border-ink/10",
                  )}
                >
                  <img src={capture.thumb_url} alt="" className="aspect-square w-full object-cover transition group-hover:opacity-85" />
                  <div className="truncate bg-paper px-2 py-1 text-[0.65rem] font-black text-ink/65">
                    {formatCaptureTime(capture.captured_at)}
                  </div>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

function DayPhotoView({
  photo,
  onToggle,
  onTogglePending,
  onDelete,
  deletePending,
  onDateSave,
  datePending,
}: {
  photo: CapturedPhoto;
  onToggle: () => void;
  onTogglePending: boolean;
  onDelete: () => void;
  deletePending: boolean;
  onDateSave: (capturedAt: string) => void;
  datePending: boolean;
}) {
  const [view, setView] = useState<"aligned" | "original">(photo.aligned_url ? "aligned" : "original");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const initialDateTime = capturedAtToInputs(photo.captured_at);
  const [dateValue, setDateValue] = useState(initialDateTime.date);
  const [timeValue, setTimeValue] = useState(initialDateTime.time);
  const src = view === "aligned" && photo.aligned_url ? photo.aligned_url : photo.image_url;
  const nextCapturedAt = dateValue && timeValue ? `${dateValue}T${timeValue}:00` : "";
  const capturedAtChanged = Boolean(nextCapturedAt) && nextCapturedAt !== capturedAtToLocalInput(photo.captured_at);

  useEffect(() => {
    setConfirmingDelete(false);
    const next = capturedAtToInputs(photo.captured_at);
    setDateValue(next.date);
    setTimeValue(next.time);
  }, [photo.hash, photo.captured_at]);

  return (
    <div>
      <div className="overflow-hidden rounded-md bg-ink">
        <img
          src={src}
          alt=""
          className="aspect-square w-full object-cover"
          onError={(event) => {
            event.currentTarget.src = photo.image_url;
          }}
        />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {photo.skipped ? <Badge tone="bad">{photo.skip_reason ?? "skipped"}</Badge> : <Badge tone="good">included</Badge>}
        {photo.quality_score != null ? <Badge>Score {photo.quality_score.toFixed(2)}</Badge> : null}
        {photo.aligned_url ? (
          <button
            className="rounded-md border border-ink/10 px-2 py-1 text-xs font-black text-ink/65 hover:border-teal/30"
            onClick={() => setView(view === "aligned" ? "original" : "aligned")}
          >
            <Eye className="mr-1 inline h-3.5 w-3.5" />
            {view === "aligned" ? "See original" : "See aligned"}
          </button>
        ) : null}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs font-semibold text-ink/55">
        <Stat label="Captured" value={formatCapturedAt(photo.captured_at)} />
        <Stat label="Yaw" value={fmt(photo.yaw)} />
        <Stat label="Pitch" value={fmt(photo.pitch)} />
        <Stat label="Roll" value={fmt(photo.roll)} />
        <Stat label="Eye open" value={fmt(photo.eye_open_ratio)} />
      </div>
      <div className="mt-4 rounded-md border border-ink/10 bg-white p-3">
        <div className="text-xs font-black uppercase tracking-[0.16em] text-ink/55">Capture date</div>
        <div className="mt-2 grid grid-cols-[1fr_auto] gap-2">
          <div className="grid grid-cols-2 gap-2">
            <input
              type="date"
              value={dateValue}
              onChange={(event) => setDateValue(event.target.value)}
              className="min-w-0 rounded-md border border-ink/10 bg-paper px-2 py-2 text-sm font-bold text-ink outline-none focus:border-teal"
            />
            <input
              type="time"
              value={timeValue}
              onChange={(event) => setTimeValue(event.target.value)}
              className="min-w-0 rounded-md border border-ink/10 bg-paper px-2 py-2 text-sm font-bold text-ink outline-none focus:border-teal"
            />
          </div>
          <Button
            size="sm"
            onClick={() => onDateSave(nextCapturedAt)}
            disabled={!capturedAtChanged || datePending}
          >
            <Save className="h-4 w-4" />
            Save
          </Button>
        </div>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Button variant="secondary" onClick={onToggle} disabled={onTogglePending}>
          {photo.skipped ? "Include" : "Exclude"}
        </Button>
        <Button
          variant="ghost"
          onClick={() => setConfirmingDelete(true)}
          disabled={deletePending}
        >
          <Trash2 className="h-4 w-4 text-coral" />
          <span className="text-coral">Delete</span>
        </Button>
      </div>
      {confirmingDelete ? (
        <div className="mt-3 rounded-md border border-coral/30 bg-coral/10 p-3">
          <div className="flex gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-coral" />
            <div className="min-w-0">
              <div className="text-sm font-black text-ink">Delete this selfie?</div>
              <p className="mt-1 text-xs font-semibold leading-5 text-ink/65">
                This removes it from the app catalog. App-owned inbox files are removed; external source photos are left on disk.
              </p>
            </div>
          </div>
          <div className="mt-3 flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setConfirmingDelete(false)} disabled={deletePending}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => {
                onDelete();
                setConfirmingDelete(false);
              }}
              disabled={deletePending}
            >
              {deletePending ? "Deleting" : "Delete"}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-ink/10 bg-white p-2">
      <div className="text-[0.6rem] font-black uppercase tracking-[0.16em] text-ink/45">{label}</div>
      <div className="mt-0.5 font-mono text-sm font-black text-ink">{value}</div>
    </div>
  );
}

function Legend({ tone, label }: { tone: "good" | "warn" | "default"; label: string }) {
  const dot = tone === "good" ? "bg-ink" : tone === "warn" ? "bg-coral/30" : "bg-ink/10";
  return (
    <div className="flex items-center gap-2">
      <span className={cn("h-3 w-3 rounded", dot)} />
      <span>{label}</span>
    </div>
  );
}

function shiftMonth({ year, month }: { year: number; month: number }, delta: number) {
  const next = new Date(year, month + delta, 1);
  return { year: next.getFullYear(), month: next.getMonth() };
}

function monthLabel(year: number, month: number) {
  return new Date(year, month, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function monthRange(year: number, month: number) {
  const first = new Date(year, month, 1);
  const last = new Date(year, month + 1, 0);
  return { start: isoDate(first), end: isoDate(last) };
}

function buildMonthCells(year: number, month: number): { date: string | null }[] {
  const first = new Date(year, month, 1);
  const last = new Date(year, month + 1, 0);
  const cells: { date: string | null }[] = [];
  for (let i = 0; i < first.getDay(); i++) cells.push({ date: null });
  for (let day = 1; day <= last.getDate(); day++) {
    cells.push({ date: isoDate(new Date(year, month, day)) });
  }
  while (cells.length % 7 !== 0) cells.push({ date: null });
  return cells;
}

function isoDate(date: Date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function formatLongDate(value: string) {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

function formatCapturedAt(value: string) {
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function formatCaptureTime(value: string) {
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function capturedAtToInputs(value: string) {
  const [date = "", rawTime = ""] = value.replace(" ", "T").split("T");
  return { date, time: rawTime.slice(0, 5) };
}

function capturedAtToLocalInput(value: string) {
  const { date, time } = capturedAtToInputs(value);
  return date && time ? `${date}T${time}:00` : "";
}

function fmt(value: number | null | undefined) {
  return value == null ? "–" : value.toFixed(2);
}

function primaryPhotoForDay(photos: CapturedPhoto[]) {
  for (let index = photos.length - 1; index >= 0; index -= 1) {
    if (!photos[index].skipped) return photos[index];
  }
  return photos[photos.length - 1];
}
