import { useState } from "react";

const DETAILS = "\n\nDetails:\n";

/** The brain puts an error's detail (git's own words, a path) after its message (serve.py); screens show it under Details. */
export function splitDetail(text: string): { message: string; detail: string } {
  const i = text.indexOf(DETAILS);
  return i < 0 ? { message: text, detail: "" } : { message: text.slice(0, i), detail: text.slice(i + DETAILS.length) };
}

/** A "Details" link that shows `text` (git's own words, a path) below it. No text, no link. */
export default function Details({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return <>
    <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>Details</button>
    {open && <p className="detail">{text}</p>}
  </>;
}
