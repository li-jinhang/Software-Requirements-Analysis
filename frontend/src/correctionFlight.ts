// Measure the part of each message that is visible inside its scrolling pane.
function visibleBounds(element: HTMLElement, feed: HTMLElement) {
  const rect = element.getBoundingClientRect();
  const pane = feed.getBoundingClientRect();
  const left = Math.max(rect.left, pane.left, 0);
  const right = Math.min(rect.right, pane.right, innerWidth);
  const top = Math.max(rect.top, pane.top, 0);
  const bottom = Math.min(rect.bottom, pane.bottom, innerHeight);
  if (right <= left || bottom <= top) return null;
  return { rect, left, right, centerY: (top + bottom) / 2 };
}

export function animateCorrection(card: HTMLElement) {
  const feed = card.closest<HTMLElement>(".feed");
  if (feed) feed.scrollTop = feed.scrollHeight;
  if (!card.animate || matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const leftFeed = card.closest(".duo")?.querySelector<HTMLElement>(".p0 .feed");
  const replies = leftFeed?.querySelectorAll<HTMLElement>(".msg.mine");
  const reply = replies?.[replies.length - 1];
  const from = leftFeed && reply && visibleBounds(reply, leftFeed);
  const to = feed && visibleBounds(card, feed);
  if (innerWidth < 800 || !from || !to || !CSS.supports("offset-path", 'path("M 0 0 Q 1 0 2 2")')) {
    const fade = card.animate([{ opacity: 0.75 }, { opacity: 1 }], 140);
    return () => fade.cancel();
  }

  const bar = document.createElement("span");
  bar.className = "correction-flight";
  bar.setAttribute("aria-hidden", "true");
  document.body.append(bar);
  const halfWidth = bar.offsetWidth / 2;
  const halfHeight = bar.offsetHeight / 2;
  const startX = from.right - Math.min(halfWidth, (from.right - from.left) / 2);
  const endX = (to.left + to.right) / 2;
  const rise = Math.min(36, Math.abs(endX - startX) * 0.08);
  const controlY = Math.max(halfHeight, Math.min(from.centerY, to.centerY) - rise);
  bar.style.offsetPath = `path("M ${startX} ${from.centerY} Q ${(startX + endX) / 2} ${controlY} ${endX} ${to.centerY}")`;

  const launch = reply.animate(
    [{ outlineColor: "transparent" }, { outlineColor: "rgb(107 126 144 / 36%)" }, { outlineColor: "transparent" }],
    { duration: 240, easing: "ease-in-out" },
  );
  const travelMs = 440;
  const flight = bar.animate(
    [
      { offsetDistance: "0%", opacity: 0.55, filter: "blur(0px)" },
      { offsetDistance: "52%", opacity: 1, filter: "blur(0.6px)", offset: 0.5 },
      { offsetDistance: "100%", opacity: 1, filter: "blur(0px)" },
    ],
    { duration: travelMs, easing: "cubic-bezier(.35,0,.2,1)", fill: "forwards" },
  );

  // The first visible slice of the real card occupies the bar's last seven pixels.
  // Clipping instead of stretching the content keeps text and formulas legible.
  const foldY = to.centerY - to.rect.top;
  const folded = `inset(${foldY - halfHeight}px ${to.rect.right - endX - halfWidth}px ${to.rect.height - foldY - halfHeight}px ${endX - to.rect.left - halfWidth}px round 4px)`;
  const open = "inset(0px 0px 0px 0px round 14px)";
  const restingColor = getComputedStyle(card).backgroundColor;
  const barColor = getComputedStyle(bar).backgroundColor;
  const reveal = card.animate(
    [
      { clipPath: folded, opacity: 0, backgroundColor: barColor },
      { clipPath: folded, opacity: 1, backgroundColor: barColor, offset: 0.04 },
      { clipPath: open, transform: "scaleY(1.008)", backgroundColor: restingColor, offset: 0.82 },
      { clipPath: open, transform: "scaleY(1)", backgroundColor: restingColor },
    ],
    { delay: travelMs - 10, duration: 260, easing: "ease-out", fill: "backwards" },
  );
  flight.onfinish = () => bar.remove();
  function cleanup() {
    window.removeEventListener("resize", cleanup);
    flight.cancel();
    launch.cancel();
    reveal.cancel();
    bar.remove();
  }
  reveal.onfinish = cleanup;
  window.addEventListener("resize", cleanup, { once: true });
  return cleanup;
}
