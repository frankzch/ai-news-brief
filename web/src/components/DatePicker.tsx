import { useId } from "react";

interface Props {
  dates: string[];
  value: string;
  onChange: (date: string) => void;
}

export function DatePicker({ dates, value, onChange }: Props) {
  const id = useId();
  return (
    <div className="date-picker">
      <label htmlFor={id}>Date</label>
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
