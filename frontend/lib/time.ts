type TimestampStyle = "date" | "time" | "datetime";

const formats: Record<TimestampStyle, Intl.DateTimeFormatOptions> = {
  date: { year: "numeric", month: "short", day: "numeric" },
  time: {
    hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short",
  },
  datetime: {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short",
  },
};

/** Display the recorded instant in the browser's local zone; never substitute now. */
export function formatTimestamp(
  value: string | null | undefined,
  style: TimestampStyle = "datetime",
): string {
  if (!value || !/T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return "Time unavailable";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, formats[style]).format(date);
}
