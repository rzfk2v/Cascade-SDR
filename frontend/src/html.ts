// HTML-escape untrusted text before interpolating it into innerHTML.
//
// Everything decoded off the air — callsigns, ship names, APRS comments, DAB
// station labels, pager/ACARS text — is attacker-controlled: anyone within RF
// range chooses those bytes, so they must never reach the DOM unescaped.
// Quotes are escaped too, making the result safe inside double- or
// single-quoted attribute values as well as element content.
export function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
  );
}
