const ESC: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
}

/** Tagged template that escapes interpolations unless they're wrapped in raw(). */
export class Raw {
  constructor(readonly value: string) {}
}

export const raw = (value: string) => new Raw(value);

export function html(strings: TemplateStringsArray, ...values: unknown[]): Raw {
  let out = strings[0];
  values.forEach((v, i) => {
    const part = Array.isArray(v) ? v.map((x) => (x instanceof Raw ? x.value : esc(x))).join("") : v instanceof Raw ? v.value : esc(v);
    out += part + strings[i + 1];
  });
  return new Raw(out);
}
