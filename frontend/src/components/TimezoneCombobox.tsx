import { ChevronDown, Search } from "lucide-react";
import { type KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from "react";

type TimezoneComboboxProps = {
  value: string;
  onChange: (timezone: string) => void;
  disabled?: boolean;
  name?: string;
};

type TimezoneOption = {
  value: string;
  offsetMinutes: number;
  offsetLabel: string;
  label: string;
  searchText: string;
};

const fallbackTimezones = [
  "UTC",
  "Europe/Paris",
  "Europe/Rome",
  "Europe/Madrid",
  "Europe/London",
  "America/Argentina/Buenos_Aires",
  "America/New_York",
  "America/Los_Angeles",
];

const supportedTimezones = getSupportedTimezones();

export function TimezoneCombobox({ value, onChange, disabled = false, name }: TimezoneComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const listboxId = useId();
  const options = useMemo(() => buildTimezoneOptions(value), [value]);
  const filteredOptions = useMemo(() => filterTimezoneOptions(options, query), [options, query]);
  const selectedOption = options.find((option) => option.value === value) ?? buildTimezoneOption(value || "UTC");
  const visibleActiveIndex = Math.min(activeIndex, Math.max(filteredOptions.length - 1, 0));
  const activeOptionId = `${listboxId}-option-${visibleActiveIndex}`;

  useEffect(() => {
    if (!open) return undefined;
    const timer = window.setTimeout(() => searchRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;

    function handlePointerDown(event: PointerEvent) {
      if (rootRef.current?.contains(event.target as Node)) return;
      setOpen(false);
      updateQuery("");
    }

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  function selectOption(option: TimezoneOption) {
    onChange(option.value);
    setOpen(false);
    updateQuery("");
  }

  function updateQuery(nextQuery: string) {
    setQuery(nextQuery);
    setActiveIndex(0);
  }

  function handleButtonKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
    }
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      updateQuery("");
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((current) => Math.min(current + 1, Math.max(filteredOptions.length - 1, 0)));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) => Math.max(current - 1, 0));
      return;
    }
    if (event.key === "Enter" && filteredOptions[visibleActiveIndex]) {
      event.preventDefault();
      selectOption(filteredOptions[visibleActiveIndex]);
    }
  }

  return (
    <div className="timezone-combobox" ref={rootRef}>
      <input type="hidden" name={name} value={value} />
      <button
        type="button"
        className="timezone-combobox__button"
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={handleButtonKeyDown}
      >
        <span>{selectedOption.label}</span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      {open ? (
        <div className="timezone-combobox__popover">
          <label className="timezone-combobox__search-wrap">
            <Search size={15} aria-hidden="true" />
            <input
              ref={searchRef}
              className="timezone-combobox__search"
              value={query}
              onChange={(event) => updateQuery(event.target.value)}
              onKeyDown={handleSearchKeyDown}
              placeholder="Buscar zona horaria..."
              role="combobox"
              aria-expanded="true"
              aria-controls={listboxId}
              aria-activedescendant={filteredOptions.length ? activeOptionId : undefined}
            />
          </label>
          <div className="timezone-combobox__list" id={listboxId} role="listbox">
            {filteredOptions.length ? (
              filteredOptions.map((option, index) => {
                const selected = option.value === value;
                const active = index === visibleActiveIndex;
                return (
                  <button
                    key={option.value}
                    id={`${listboxId}-option-${index}`}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    className={[
                      "timezone-combobox__option",
                      active ? "timezone-combobox__option--active" : "",
                      selected ? "timezone-combobox__option--selected" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => selectOption(option)}
                  >
                    <span className="timezone-combobox__offset">{option.offsetLabel}</span>
                    <span className="timezone-combobox__name">{option.value}</span>
                  </button>
                );
              })
            ) : (
              <div className="timezone-combobox__empty">Sin resultados</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function formatTimezoneLabel(timeZone: string, date = new Date()) {
  return `${formatTimezoneOffset(timeZone, date)} — ${timeZone}`;
}

export function formatTimezoneOffset(timeZone: string, date = new Date()) {
  const minutes = getTimeZoneOffsetMinutes(timeZone, date);
  return formatOffsetMinutes(minutes);
}

function formatOffsetMinutes(minutes: number) {
  const sign = minutes >= 0 ? "+" : "-";
  const absolute = Math.abs(minutes);
  const hours = Math.floor(absolute / 60);
  const mins = absolute % 60;
  return `UTC${sign}${String(hours).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
}

function buildTimezoneOptions(currentValue: string) {
  const values = new Set([...supportedTimezones, ...fallbackTimezones]);
  if (currentValue) values.add(currentValue);
  const date = new Date();
  return Array.from(values)
    .map((timeZone) => buildTimezoneOption(timeZone, date))
    .sort((a, b) => a.offsetMinutes - b.offsetMinutes || a.value.localeCompare(b.value));
}

function buildTimezoneOption(timeZone: string, date = new Date()): TimezoneOption {
  const offsetMinutes = getTimeZoneOffsetMinutes(timeZone, date);
  const offsetLabel = formatOffsetMinutes(offsetMinutes);
  const parts = timeZone.split("/");
  const city = (parts[parts.length - 1] || timeZone).replace(/_/g, " ");
  const searchText = normalizeTimezoneSearch(`${timeZone} ${timeZone.replace(/_/g, " ")} ${city} ${offsetLabel} ${offsetLabel.replace("UTC", "")}`);
  return {
    value: timeZone,
    offsetMinutes,
    offsetLabel,
    label: `${offsetLabel} — ${timeZone}`,
    searchText,
  };
}

function filterTimezoneOptions(options: TimezoneOption[], query: string) {
  const normalizedQuery = normalizeTimezoneSearch(query);
  if (!normalizedQuery) return options;
  return options.filter((option) => option.searchText.includes(normalizedQuery));
}

function normalizeTimezoneSearch(value: string) {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/_/g, " ")
    .toLowerCase()
    .trim();
}

function getSupportedTimezones() {
  const intlWithValues = Intl as typeof Intl & { supportedValuesOf?: (key: "timeZone") => string[] };
  const values = intlWithValues.supportedValuesOf?.("timeZone") ?? [];
  return values.includes("UTC") ? values : ["UTC", ...values];
}

function getTimeZoneOffsetMinutes(timeZone: string, date = new Date()) {
  const shortOffset = getShortOffsetMinutes(timeZone, date);
  if (shortOffset !== null) return shortOffset;
  return getPartsOffsetMinutes(timeZone, date);
}

function getShortOffsetMinutes(timeZone: string, date: Date) {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hour: "2-digit",
      timeZoneName: "shortOffset",
    }).formatToParts(date);
    const value = parts.find((part) => part.type === "timeZoneName")?.value;
    if (!value || value === "GMT") return 0;
    const match = value.match(/^GMT([+-])(\d{1,2})(?::?(\d{2}))?$/);
    if (!match) return null;
    const sign = match[1] === "+" ? 1 : -1;
    return sign * (Number(match[2]) * 60 + Number(match[3] ?? 0));
  } catch {
    return null;
  }
}

function getPartsOffsetMinutes(timeZone: string, date: Date) {
  try {
    const dtf = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    const parts = dtf.formatToParts(date);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    const hour = Number(values.hour) === 24 ? 0 : Number(values.hour);
    const asUTC = Date.UTC(
      Number(values.year),
      Number(values.month) - 1,
      Number(values.day),
      hour,
      Number(values.minute),
      Number(values.second),
    );
    return Math.round((asUTC - date.getTime()) / 60000);
  } catch {
    return 0;
  }
}
