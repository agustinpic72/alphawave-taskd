import { BadgeCheck, Check, ChevronDown, Search } from "lucide-react";
import { type KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from "react";

import type { OpenAIModelOption } from "../api";

type Props = {
  value: string;
  options: OpenAIModelOption[];
  recommendedModel?: string | null;
  disabled?: boolean;
  onChange: (model: string) => void;
};

export function OpenAIModelCombobox({
  value,
  options,
  recommendedModel,
  disabled = false,
  onChange,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const listboxId = useId();
  const selected = options.find((option) => option.id === value);
  const filtered = useMemo(() => {
    const normalized = normalize(query);
    if (!normalized) return options;
    return options.filter((option) =>
      normalize(`${option.display_name} ${option.id} ${option.category} ${option.recommendation ?? ""}`).includes(normalized),
    );
  }, [options, query]);
  const visibleActiveIndex = Math.min(activeIndex, Math.max(filtered.length - 1, 0));

  useEffect(() => {
    if (!open) return undefined;
    const timer = window.setTimeout(() => searchRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
        updateQuery("");
      }
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  function choose(option: OpenAIModelOption) {
    if (option.compatibility === "unavailable") return;
    onChange(option.id);
    setOpen(false);
    updateQuery("");
    window.setTimeout(() => buttonRef.current?.focus(), 0);
  }

  function updateQuery(nextQuery: string) {
    setQuery(nextQuery);
    setActiveIndex(0);
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      updateQuery("");
      window.setTimeout(() => buttonRef.current?.focus(), 0);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((current) => Math.min(current + 1, Math.max(filtered.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) => Math.max(current - 1, 0));
    } else if (event.key === "Enter" && filtered[visibleActiveIndex]) {
      event.preventDefault();
      choose(filtered[visibleActiveIndex]);
    }
  }

  return (
    <div className="model-combobox" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="model-combobox__button"
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        data-testid="openai-model-selector"
      >
        <span>
          <strong>{selected?.display_name || value || "Elegí un modelo"}</strong>
          {selected && selected.display_name !== selected.id ? <small>{selected.id}</small> : null}
        </span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      {open ? (
        <div className="model-combobox__popover">
          <label className="model-combobox__search-wrap">
            <Search size={15} aria-hidden="true" />
            <span className="sr-only">Buscar modelo</span>
            <input
              ref={searchRef}
              value={query}
              onChange={(event) => updateQuery(event.target.value)}
              onKeyDown={handleSearchKeyDown}
              placeholder="Buscar modelo..."
              aria-controls={listboxId}
              aria-activedescendant={filtered.length ? `${listboxId}-${visibleActiveIndex}` : undefined}
            />
          </label>
          <div id={listboxId} className="model-combobox__list" role="listbox">
            {filtered.length ? filtered.map((option, index) => {
              const unavailable = option.compatibility === "unavailable";
              return (
                <button
                  key={option.id}
                  id={`${listboxId}-${index}`}
                  type="button"
                  role="option"
                  aria-selected={option.id === value}
                  aria-disabled={unavailable}
                  className={`model-combobox__option ${index === visibleActiveIndex ? "is-active" : ""}`}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => choose(option)}
                >
                  <span className="model-combobox__option-copy">
                    <strong>{option.display_name}</strong>
                    {option.display_name !== option.id ? <small>{option.id} · {option.category}</small> : null}
                    {option.recommendation ? <small>{option.recommendation}</small> : null}
                  </span>
                  <span className="model-combobox__badges">
                    {option.id === recommendedModel ? <span>Recomendado</span> : null}
                    {option.is_validated ? <span><BadgeCheck size={13} /> Validado</span> : null}
                    {unavailable ? <span className="warning">No disponible</span> : null}
                    {option.id === value ? <Check size={16} aria-hidden="true" /> : null}
                  </span>
                </button>
              );
            }) : <div className="model-combobox__empty">Sin modelos compatibles</div>}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function normalize(value: string) {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
}
