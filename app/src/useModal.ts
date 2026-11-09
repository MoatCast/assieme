import { useEffect, useRef } from "react";

/** What a modal sheet needs: focus on its primary action (or the sheet) whenever `refocus` changes, focus back to the
 *  opener on close, Escape to `onEscape` (except in a terminal, where it belongs to the program), and Tab kept inside. */
export default function useModal(onEscape: () => void, refocus: unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    return () => opener?.focus?.();
  }, []);
  useEffect(() => {
    const primary = ref.current?.querySelector<HTMLElement>(".sheet-actions .btn-primary:not(:disabled)");
    (primary ?? ref.current)?.focus();
  }, refocus); // eslint-disable-line react-hooks/exhaustive-deps

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape" && !(e.target as HTMLElement).closest(".sheet-terminal")) {
      e.stopPropagation();
      onEscape();
    } else if (e.key === "Tab") {
      const items = [...(ref.current?.querySelectorAll<HTMLElement>("button:not(:disabled), textarea, [tabindex]:not([tabindex='-1'])") ?? [])];
      const [first, last] = [items[0], items[items.length - 1]];
      if (!first) return;
      if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };
  return { ref, onKeyDown };
}
