/**
 * SymbolLink.tsx — canonical link to the symbol detail page (P1 §2).
 *
 * Renders `<Link href={{pathname:'/symbol', query:{c}}}>` so codes route to
 * /symbol?c=CODE via client-side navigation (SSE/SWR caches survive). Codes
 * are uppercase-normalized. Styling inherits from the surrounding cell
 * (Tailwind preflight resets anchors to color:inherit) plus hover underline.
 */
import Link from 'next/link';
import type { ReactNode } from 'react';

interface Props {
  code: string;
  className?: string;
  children?: ReactNode;
}

export default function SymbolLink({ code, className, children }: Props) {
  const c = code.toUpperCase();
  return (
    <Link
      href={{ pathname: '/symbol', query: { c } }}
      data-testid={`symbol-link-${c}`}
      className={`hover:underline${className ? ` ${className}` : ''}`}
    >
      {children ?? c}
    </Link>
  );
}
