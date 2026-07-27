import { Time } from "@internationalized/date";
import {
  DateInput,
  DateSegment,
  FieldError,
  Label,
  TimeField as AriaTimeField,
} from "react-aria-components";

type AppTimeFieldProps = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label?: string;
  name?: string;
  error?: string;
  fallbackValue?: string;
  "data-testid"?: string;
};
type TimeLike = {
  hour: number;
  minute: number;
};

const defaultFallback = "09:00";
const invalidTimeMessage = "Usá formato HH:mm, por ejemplo 09:00 o 13:30.";

export function TimeField({
  value,
  onChange,
  disabled = false,
  label,
  name,
  error,
  fallbackValue = defaultFallback,
  "data-testid": dataTestId,
}: AppTimeFieldProps) {
  const parsedValue = parseHHmmToTimeValue(value);
  const fallback = parseHHmmToTimeValue(fallbackValue) ?? parseHHmmToTimeValue(defaultFallback);
  const safeValue = parsedValue ?? fallback;
  const validationError = error ?? (value && !parsedValue ? invalidTimeMessage : null);

  return (
    <AriaTimeField
      className="time-field"
      value={safeValue}
      onChange={(nextValue) => {
        if (!nextValue) return;
        onChange(formatTimeValueToHHmm(nextValue));
      }}
      granularity="minute"
      hourCycle={24}
      shouldForceLeadingZeros
      placeholderValue={fallback ?? undefined}
      isDisabled={disabled}
      isInvalid={Boolean(validationError)}
      data-testid={dataTestId}
    >
      {label ? <Label className="time-field__label">{label}</Label> : null}
      <DateInput className="time-field__input">
        {(segment) => <DateSegment className="time-field__segment" segment={segment} />}
      </DateInput>
      {name ? <input type="hidden" name={name} value={safeValue ? formatTimeValueToHHmm(safeValue) : ""} /> : null}
      <FieldError className="time-field__error">{validationError}</FieldError>
    </AriaTimeField>
  );
}

export function parseHHmmToTimeValue(value: string) {
  const match = value.trim().match(/^(\d{2}):(\d{2})$/);
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return null;
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return new Time(hour, minute);
}

export function formatTimeValueToHHmm(value: TimeLike) {
  return `${String(value.hour).padStart(2, "0")}:${String(value.minute).padStart(2, "0")}`;
}

export function isValidTime(value: string) {
  return parseHHmmToTimeValue(value) !== null;
}
