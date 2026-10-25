/**
 * Position Helpers
 *
 * Utility functions for calculating agent positions in the office:
 *   - Desk positions based on desk number
 *   - Elevator target positions
 *
 * Extracted from agentMachineService.ts for testability and reuse.
 */

import type { Position } from "@/types";
import { deskSeatPosition } from "@/constants/officeLayout";
import {
  ARRIVAL_QUEUE_POSITIONS,
  DEPARTURE_QUEUE_POSITIONS,
  ELEVATOR_PATHFINDING_TARGET,
} from "@/systems/queuePositions";

// ============================================================================
// DESK POSITION
// ============================================================================

/**
 * Calculate the pixel position for an agent seated at a given desk.
 *
 * Geometry lives in `constants/officeLayout.ts` (shared with the desk
 * renderer and the navigation grid).
 *
 * @param deskNum - 1-based desk number
 * @returns Pixel position for the agent's bottom circle centre
 */
export function getDeskPosition(deskNum: number): Position {
  return deskSeatPosition(deskNum);
}

// ============================================================================
// QUEUE / READY POSITION HELPERS
// ============================================================================

/**
 * Return the A0 / D0 position where agents stand when talking to the boss.
 *
 * @param queueType - Which queue the agent belongs to
 */
export function getReadyPosition(queueType: "arrival" | "departure"): Position {
  return queueType === "arrival"
    ? ARRIVAL_QUEUE_POSITIONS[0]
    : DEPARTURE_QUEUE_POSITIONS[0];
}

// ============================================================================
// ELEVATOR POSITION HELPERS
// ============================================================================

/**
 * Return the pathfinding target for agents walking to the elevator.
 *
 * This is the cell that the pathfinder navigates toward; the agent
 * snaps to the true departure position once inside the elevator.
 */
export function getElevatorPathTarget(): Position {
  return ELEVATOR_PATHFINDING_TARGET;
}
