import { useLang } from "../i18n/LangContext";
import { useId } from "react";

interface Props {
  dates: string[];
  value: string;
  onChange: (date: string) => void;
}

export function DatePicker({ dates, value, onChange }: Props) {
  const id = useId();
  const { t } = useLang();
  return (
    <div className="date-picker">
      <label htmlFor={id}>{t.date}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {dates.map((d) => (
          <option key={d} value={d}>
            {d}
          </option>
        ))}
      </select>
    </div>
  );
}
