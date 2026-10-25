/**
 * Office layout: annex desk rows below the boss area, canvas growth, and the
 * navigation grid staying in sync with the rendered height.
 */

import { createActor } from "xstate";
import { describe, expect, it, vi } from "vitest";
import {
  ANNEX_ORIGIN_Y,
  MAX_DESKS,
  MAX_OFFICE_HEIGHT,
  deskOrigin,
  deskSeatPosition,
  officeHeight,
  visibleDeskCount,
} from "@/constants/officeLayout";
import { CANVAS_HEIGHT } from "@/constants/canvas";
import { NavigationGrid, TILE_SIZE } from "@/systems/navigationGrid";
import { getDeskPosition as helperDeskPosition } from "@/machines/positionHelpers";
import { getDeskPosition as queueDeskPosition } from "@/systems/queuePositions";
import { createAgentMachine } from "@/machines/agentMachine";
import type { AgentMachineActions } from "@/machines/agentMachineCommon";

describe("desk geometry", () => {
  it("keeps the original two rows where they always were", () => {
    expect(deskOrigin(1)).toEqual({ x: 256, y: 408 });
    expect(deskOrigin(4)).toEqual({ x: 1024, y: 408 });
    expect(deskOrigin(5)).toEqual({ x: 256, y: 600 });
    expect(deskSeatPosition(1)).toEqual({ x: 256, y: 432 });
  });

  it("places row three and beyond in the annex below the boss area", () => {
    expect(deskOrigin(9)).toEqual({ x: 256, y: ANNEX_ORIGIN_Y });
    expect(deskOrigin(13).y).toBe(ANNEX_ORIGIN_Y + 192);
  });

  it("all desk-position helpers agree with the shared layout", () => {
    for (const desk of [1, 6, 9, 24, MAX_DESKS]) {
      expect(helperDeskPosition(desk)).toEqual(deskSeatPosition(desk));
      expect(queueDeskPosition(desk)).toEqual(deskSeatPosition(desk));
    }
  });
});

describe("office size", () => {
  it("renders whole rows for agents and occupied desks, capped at MAX_DESKS", () => {
    expect(visibleDeskCount(0, 0)).toBe(8);
    expect(visibleDeskCount(9, 0)).toBe(12);
    expect(visibleDeskCount(2, 17)).toBe(20); // a lone agent at desk 17
    expect(visibleDeskCount(200, 0)).toBe(MAX_DESKS);
  });

  it("grows the canvas only once the annex is needed", () => {
    expect(officeHeight(8)).toBe(CANVAS_HEIGHT);
    expect(officeHeight(12)).toBeGreaterThan(CANVAS_HEIGHT);
    expect(officeHeight(24)).toBe(ANNEX_ORIGIN_Y + 3 * 192 + 192);
    expect(officeHeight(MAX_DESKS)).toBe(MAX_OFFICE_HEIGHT);
  });

  it("walls off the floor below the current office height", () => {
    const grid = new NavigationGrid();
    const below = Math.ceil(CANVAS_HEIGHT / TILE_SIZE) + 2;
    const aisleX = Math.floor(384 / TILE_SIZE); // between desk columns
    expect(grid.isWalkable(aisleX, below)).toBe(false);

    grid.setOfficeHeight(officeHeight(24));
    expect(grid.isWalkable(aisleX, below)).toBe(true);
    // Annex desks are obstacles; their chairs are not.
    const annexDesk = grid.worldToGrid(256, ANNEX_ORIGIN_Y + 100);
    expect(grid.isWalkable(annexDesk.gx, annexDesk.gy)).toBe(false);
    const annexSeat = grid.worldToGrid(256, deskSeatPosition(9).y);
    expect(grid.isWalkable(annexSeat.gx, annexSeat.gy)).toBe(true);
  });
});

describe("express lane", () => {
  function actions(
    express: boolean,
  ): AgentMachineActions & { phases: string[] } {
    const phases: string[] = [];
    const noop = () => {};
    return {
      onStartWalking: noop,
      onQueueJoined: noop,
      onQueueLeft: noop,
      onPhaseChanged: (_id, phase) => phases.push(phase),
      onShowBossBubble: noop,
      onShowAgentBubble: noop,
      onClearBossBubble: noop,
      onClearAgentBubble: noop,
      onSetBossInUse: noop,
      onOpenElevator: noop,
      onCloseElevator: noop,
      onAgentRemoved: noop,
      shouldTakeExpressLane: () => express,
      phases,
    };
  }

  it("sends arrivals straight to their desk when the queue is long", () => {
    vi.useFakeTimers();
    const rec = actions(true);
    const actor = createActor(createAgentMachine(rec)).start();
    actor.send({
      type: "SPAWN",
      agentId: "A",
      name: "A",
      desk: 9,
      position: { x: 0, y: 0 },
    });
    expect(rec.phases).toEqual(["walking_to_desk"]);
    actor.send({ type: "ARRIVED_AT_DESK" });
    expect(actor.getSnapshot().value).toBe("idle");
  });

  it("sends departures straight to the elevator when the queue is long", () => {
    vi.useFakeTimers();
    const rec = actions(true);
    const actor = createActor(createAgentMachine(rec)).start();
    actor.send({
      type: "SPAWN_AT_DESK",
      agentId: "A",
      name: "A",
      desk: 1,
      position: { x: 0, y: 0 },
    });
    actor.send({ type: "REMOVE" });
    expect(rec.phases.at(-1)).toBe("walking_to_elevator");
    actor.send({ type: "ARRIVED_AT_ELEVATOR" });
    expect(actor.getSnapshot().value).toEqual({ departure: "in_elevator" });
  });

  it("keeps the boss handover when the queue is short", () => {
    vi.useFakeTimers();
    const rec = actions(false);
    const actor = createActor(createAgentMachine(rec)).start();
    actor.send({
      type: "SPAWN",
      agentId: "A",
      name: "A",
      desk: 1,
      position: { x: 0, y: 0 },
    });
    expect(rec.phases).toEqual(["arriving"]);
  });
});
