/**
 * Office floor layout — single source of truth for desk geometry.
 *
 * The first two desk rows sit between the wall and the boss area, as they
 * always have. Larger teams get extra rows in an annex *below* the boss area,
 * so every existing position (boss, rug, printer, queues, elevator) stays put
 * and the canvas simply grows downward. The canvas is CSS-scaled with
 * `object-fit: contain`, so a taller office is automatically zoomed to fit.
 */

import type { Position } from "@/types";
import { CANVAS_HEIGHT } from "./canvas";
import { DESKS_PER_ROW, MIN_DESK_COUNT } from "./positions";

/** Centre X of each desk column (multiples of the 32px nav tile). */
export const DESK_COLUMN_X = [256, 512, 768, 1024] as const;

/** Desk sprite origin Y of row 0, and the spacing between rows. */
export const DESK_ORIGIN_Y = 408;
export const DESK_ROW_STEP = 192;

/** Rows above the boss area; further rows go into the annex. */
export const BASE_DESK_ROWS = 2;

/** Desk origin Y of the first annex row (below the boss desk at y≈1015). */
export const ANNEX_ORIGIN_Y = 1120;

/** Agent seat offset from the desk origin (bottom circle centre on the chair). */
export const SEAT_OFFSET_Y = 24;

/** Space below a row's desk origin: desk surface ends at +128, plus margin. */
const ROW_BOTTOM_MARGIN = 192;

/** Upper bound of desks on the floor — keep in sync with backend MAX_AGENTS. */
export const MAX_DESKS = 48;

export function deskRowCount(deskCount: number): number {
  return Math.ceil(Math.max(deskCount, 1) / DESKS_PER_ROW);
}

/** Desk sprite origin Y for a 0-based row. */
export function deskRowOriginY(row: number): number {
  return row < BASE_DESK_ROWS
    ? DESK_ORIGIN_Y + row * DESK_ROW_STEP
    : ANNEX_ORIGIN_Y + (row - BASE_DESK_ROWS) * DESK_ROW_STEP;
}

/** Desk sprite origin for a 1-based desk number. */
export function deskOrigin(deskNum: number): Position {
  const index = Math.max(deskNum, 1) - 1;
  return {
    x: DESK_COLUMN_X[index % DESKS_PER_ROW],
    y: deskRowOriginY(Math.floor(index / DESKS_PER_ROW)),
  };
}

/** Where an agent's bottom circle centre sits when seated at a desk. */
export function deskSeatPosition(deskNum: number): Position {
  const origin = deskOrigin(deskNum);
  return { x: origin.x, y: origin.y + SEAT_OFFSET_Y };
}

/** Desks to draw: whole rows covering every agent and every occupied desk. */
export function visibleDeskCount(
  agentCount: number,
  highestDesk: number,
): number {
  const needed = Math.max(agentCount, highestDesk, MIN_DESK_COUNT);
  return Math.min(MAX_DESKS, deskRowCount(needed) * DESKS_PER_ROW);
}

/** Canvas height for a given desk count. */
export function officeHeight(deskCount: number): number {
  const rows = deskRowCount(deskCount);
  if (rows <= BASE_DESK_ROWS) return CANVAS_HEIGHT;
  return deskRowOriginY(rows - 1) + ROW_BOTTOM_MARGIN;
}

/** Tallest possible office, used to size the navigation grid. */
export const MAX_OFFICE_HEIGHT = officeHeight(MAX_DESKS);
