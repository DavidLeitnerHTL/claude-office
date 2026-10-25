/**
 * Grid-based navigation system for office pathfinding.
 *
 * Converts the pixel-based office layout into a tile grid for A* pathfinding.
 * Supports static obstacles (walls, desks) and dynamic obstacles (agents, furniture).
 */

import { Position } from "@/types";
import { CANVAS_HEIGHT } from "@/constants/canvas";
import {
  DESK_COLUMN_X,
  MAX_DESKS,
  MAX_OFFICE_HEIGHT,
  deskRowCount,
  deskRowOriginY,
} from "@/constants/officeLayout";
import { ELEVATOR_ZONE } from "./queuePositions";

// Grid configuration
export const TILE_SIZE = 32;
export const GRID_WIDTH = 40; // 1280 / 32
// Sized for the tallest office (annex desk rows below the boss area); rows
// below the current office height are walled off via setOfficeHeight().
export const GRID_HEIGHT = Math.ceil(MAX_OFFICE_HEIGHT / TILE_SIZE);

// Tile types for pathfinding
export enum TileType {
  FLOOR = 0, // Walkable, base cost 1.0
  WALL = 1, // Impassable
  DESK = 2, // Impassable (desk surface)
  ELEVATOR = 3, // Conditionally passable (arrivals/departures)
  BOSS_DESK = 4, // Impassable (boss desk)
  AGENT = 5, // Temporary obstacle (other agents)
}

// Movement costs for different tile types
export const TILE_COSTS: Record<TileType, number> = {
  [TileType.FLOOR]: 1.0,
  [TileType.WALL]: Infinity,
  [TileType.DESK]: Infinity,
  [TileType.ELEVATOR]: 1.0, // Passable for arrivals/departures
  [TileType.BOSS_DESK]: Infinity,
  [TileType.AGENT]: 50.0, // High cost to encourage avoidance
};

// Office layout constants (in pixels)
// Agent sprite radius is ~28px, padding = 55% of that = ~15px
const OBSTACLE_PADDING = 15;

const WALL_Y_END = 232 + OBSTACLE_PADDING; // Wall visual end + padding (one extra row)
// Desk obstacles cover the desk SURFACE with padding; chairs stay walkable
// as destinations. The surface spans origin+80 .. origin+128 (row 0: 488-536,
// one tile trimmed off the top so agents can walk closer to desks).
const DESK_SURFACE_TOP = 80;
const DESK_SURFACE_BOTTOM = 128;
const DESK_HALF_WIDTH = 70 + OBSTACLE_PADDING; // Desk visual half-width + padding

// Elevator bounds derived from ELEVATOR_ZONE (single source of truth)
const ELEVATOR_X = (ELEVATOR_ZONE.minX + ELEVATOR_ZONE.maxX) / 2; // 76
const ELEVATOR_Y = ELEVATOR_ZONE.minY; // 90
const ELEVATOR_WIDTH = ELEVATOR_ZONE.maxX - ELEVATOR_ZONE.minX; // 112
const ELEVATOR_HEIGHT = ELEVATOR_ZONE.maxY - ELEVATOR_ZONE.minY; // 210

const BOSS_DESK_X = 640;
// Boss at y=900, desk drawn 20px below with 80px height → desk center at y=960
const BOSS_DESK_Y = 960; // Grid-aligned: 30*32 = 960
const BOSS_DESK_HALF_WIDTH = 80 + OBSTACLE_PADDING;
const BOSS_DESK_HALF_HEIGHT = 40 + OBSTACLE_PADDING;

// Printer station (bottom left corner) - only bottom portion blocked
const PRINTER_X = 50;
const PRINTER_Y = 993;
const PRINTER_HALF_WIDTH = 50 + OBSTACLE_PADDING;
const PRINTER_HALF_HEIGHT = 12;

// Trash can (right of boss desk)
const TRASH_CAN_X = 640 + 110; // Boss position.x + offset
const TRASH_CAN_Y = 900 + 65 + 20; // Boss position.y + offset + bottom adjustment
const TRASH_CAN_HALF_WIDTH = 20 + OBSTACLE_PADDING;
const TRASH_CAN_HALF_HEIGHT = 15;

export interface GridPosition {
  gx: number;
  gy: number;
}

/**
 * Minimal grid surface the A* pathfinder depends on. Implemented by the office
 * {@link NavigationGrid} and by the Command Center grid, so A* can run on either.
 */
export interface PathGrid {
  worldToGrid(x: number, y: number): GridPosition;
  gridToWorld(gx: number, gy: number): Position;
  isWalkable(gx: number, gy: number, ignoreAgentId?: string): boolean;
  getCost(gx: number, gy: number, ignoreAgentId?: string): number;
  getNeighbors(gx: number, gy: number): GridPosition[];
}

export interface DynamicObstacle {
  type: "agent" | "furniture";
  agentId?: string;
  tiles: GridPosition[];
  expiresAt?: number;
}

/**
 * Navigation grid for A* pathfinding.
 */
export class NavigationGrid {
  private staticGrid: Uint8Array;
  private dynamicObstacles: Map<string, DynamicObstacle> = new Map();
  private officeHeight = CANVAS_HEIGHT;

  constructor() {
    this.staticGrid = new Uint8Array(GRID_WIDTH * GRID_HEIGHT);
    this.initializeStaticGrid();
  }

  /**
   * Match the walkable area to the rendered office height. Everything below
   * it is wall, so paths never leave the visible floor.
   */
  setOfficeHeight(height: number): void {
    if (height === this.officeHeight) return;
    this.officeHeight = height;
    this.initializeStaticGrid();
  }

  /**
   * Initialize the static obstacle grid from office layout.
   */
  private initializeStaticGrid(): void {
    // Mark all tiles as floor initially
    this.staticGrid.fill(TileType.FLOOR);

    // Mark wall area (top of office)
    for (let gx = 0; gx < GRID_WIDTH; gx++) {
      for (let gy = 0; gy < Math.ceil(WALL_Y_END / TILE_SIZE); gy++) {
        this.setTile(gx, gy, TileType.WALL);
      }
    }

    // Mark elevator as passable (special handling in pathfinding)
    const elevatorStartGx = Math.floor(
      (ELEVATOR_X - ELEVATOR_WIDTH / 2) / TILE_SIZE,
    );
    const elevatorEndGx = Math.ceil(
      (ELEVATOR_X + ELEVATOR_WIDTH / 2) / TILE_SIZE,
    );
    const elevatorStartGy = Math.floor(ELEVATOR_Y / TILE_SIZE);
    const elevatorEndGy = Math.ceil((ELEVATOR_Y + ELEVATOR_HEIGHT) / TILE_SIZE);

    for (let gx = elevatorStartGx; gx < elevatorEndGx; gx++) {
      for (let gy = elevatorStartGy; gy < elevatorEndGy; gy++) {
        if (this.isValidTile(gx, gy)) {
          this.setTile(gx, gy, TileType.ELEVATOR);
        }
      }
    }

    // Mark desks as obstacles (every possible row; rows below the office
    // height are walled off at the end anyway)
    for (let row = 0; row < deskRowCount(MAX_DESKS); row++) {
      const origin = deskRowOriginY(row);
      for (const deskX of DESK_COLUMN_X) {
        this.markRectangle(
          deskX - DESK_HALF_WIDTH,
          origin + DESK_SURFACE_TOP - OBSTACLE_PADDING,
          deskX + DESK_HALF_WIDTH,
          origin + DESK_SURFACE_BOTTOM + OBSTACLE_PADDING,
          TileType.DESK,
        );
      }
    }

    // Mark boss desk as obstacle
    this.markRectangle(
      BOSS_DESK_X - BOSS_DESK_HALF_WIDTH,
      BOSS_DESK_Y - BOSS_DESK_HALF_HEIGHT,
      BOSS_DESK_X + BOSS_DESK_HALF_WIDTH,
      BOSS_DESK_Y + BOSS_DESK_HALF_HEIGHT,
      TileType.BOSS_DESK,
    );

    // Mark printer station as obstacle
    this.markRectangle(
      PRINTER_X - PRINTER_HALF_WIDTH,
      PRINTER_Y - PRINTER_HALF_HEIGHT,
      PRINTER_X + PRINTER_HALF_WIDTH,
      PRINTER_Y + PRINTER_HALF_HEIGHT,
      TileType.WALL, // Use WALL type for impassable
    );

    // Mark trash can as obstacle
    this.markRectangle(
      TRASH_CAN_X - TRASH_CAN_HALF_WIDTH,
      TRASH_CAN_Y - TRASH_CAN_HALF_HEIGHT,
      TRASH_CAN_X + TRASH_CAN_HALF_WIDTH,
      TRASH_CAN_Y + TRASH_CAN_HALF_HEIGHT,
      TileType.WALL, // Use WALL type for impassable
    );

    // Wall off everything below the current office height
    for (
      let gy = Math.ceil(this.officeHeight / TILE_SIZE);
      gy < GRID_HEIGHT;
      gy++
    ) {
      for (let gx = 0; gx < GRID_WIDTH; gx++) {
        this.setTile(gx, gy, TileType.WALL);
      }
    }
  }

  /**
   * Mark a rectangular area with a tile type.
   */
  private markRectangle(
    x1: number,
    y1: number,
    x2: number,
    y2: number,
    type: TileType,
  ): void {
    const startGx = Math.floor(x1 / TILE_SIZE);
    const endGx = Math.ceil(x2 / TILE_SIZE);
    const startGy = Math.floor(y1 / TILE_SIZE);
    const endGy = Math.ceil(y2 / TILE_SIZE);

    for (let gx = startGx; gx < endGx; gx++) {
      for (let gy = startGy; gy < endGy; gy++) {
        if (this.isValidTile(gx, gy)) {
          this.setTile(gx, gy, type);
        }
      }
    }
  }

  /**
   * Convert world coordinates to grid coordinates.
   */
  worldToGrid(x: number, y: number): GridPosition {
    return {
      gx: Math.floor(x / TILE_SIZE),
      gy: Math.floor(y / TILE_SIZE),
    };
  }

  /**
   * Convert grid coordinates to world coordinates (center of tile).
   */
  gridToWorld(gx: number, gy: number): Position {
    return {
      x: gx * TILE_SIZE + TILE_SIZE / 2,
      y: gy * TILE_SIZE + TILE_SIZE / 2,
    };
  }

  /**
   * Check if a tile is within grid bounds.
   */
  isValidTile(gx: number, gy: number): boolean {
    return gx >= 0 && gx < GRID_WIDTH && gy >= 0 && gy < GRID_HEIGHT;
  }

  /**
   * Get tile index from grid coordinates.
   */
  private getTileIndex(gx: number, gy: number): number {
    return gy * GRID_WIDTH + gx;
  }

  /**
   * Set a tile type at grid coordinates.
   */
  private setTile(gx: number, gy: number, type: TileType): void {
    if (this.isValidTile(gx, gy)) {
      this.staticGrid[this.getTileIndex(gx, gy)] = type;
    }
  }

  /**
   * Get the static tile type at grid coordinates.
   */
  getStaticTile(gx: number, gy: number): TileType {
    if (!this.isValidTile(gx, gy)) {
      return TileType.WALL;
    }
    return this.staticGrid[this.getTileIndex(gx, gy)] as TileType;
  }

  /**
   * Check if a tile is walkable (considering both static and dynamic obstacles).
   */
  isWalkable(gx: number, gy: number, ignoreAgentId?: string): boolean {
    const staticType = this.getStaticTile(gx, gy);

    // Impassable static tiles
    if (
      staticType === TileType.WALL ||
      staticType === TileType.DESK ||
      staticType === TileType.BOSS_DESK
    ) {
      return false;
    }

    // Check dynamic obstacles
    for (const [, obstacle] of this.dynamicObstacles) {
      if (ignoreAgentId && obstacle.agentId === ignoreAgentId) {
        continue;
      }
      for (const tile of obstacle.tiles) {
        if (tile.gx === gx && tile.gy === gy) {
          return false;
        }
      }
    }

    return true;
  }

  /**
   * Get the movement cost for a tile.
   */
  getCost(gx: number, gy: number, ignoreAgentId?: string): number {
    const staticType = this.getStaticTile(gx, gy);
    let cost = TILE_COSTS[staticType];

    // Check for dynamic agent obstacles (add high cost instead of blocking)
    for (const [, obstacle] of this.dynamicObstacles) {
      if (ignoreAgentId && obstacle.agentId === ignoreAgentId) {
        continue;
      }
      if (obstacle.type === "agent") {
        for (const tile of obstacle.tiles) {
          if (tile.gx === gx && tile.gy === gy) {
            cost += TILE_COSTS[TileType.AGENT];
          }
        }
      }
    }

    return cost;
  }

  /**
   * Add a dynamic obstacle (agent or furniture).
   */
  addDynamicObstacle(id: string, obstacle: DynamicObstacle): void {
    this.dynamicObstacles.set(id, obstacle);
  }

  /**
   * Remove a dynamic obstacle.
   */
  removeDynamicObstacle(id: string): void {
    this.dynamicObstacles.delete(id);
  }

  /**
   * Update an agent's position as a dynamic obstacle.
   */
  updateAgentPosition(agentId: string, position: Position): void {
    const gridPos = this.worldToGrid(position.x, position.y);
    this.addDynamicObstacle(`agent_${agentId}`, {
      type: "agent",
      agentId,
      tiles: [gridPos],
    });
  }

  /**
   * Clear all dynamic obstacles (useful for reset).
   */
  clearDynamicObstacles(): void {
    this.dynamicObstacles.clear();
  }

  /**
   * Get all neighbors of a tile for A* pathfinding.
   * Supports 8-directional movement.
   */
  getNeighbors(gx: number, gy: number): GridPosition[] {
    const neighbors: GridPosition[] = [];
    const directions = [
      { dx: 0, dy: -1 }, // Up
      { dx: 1, dy: 0 }, // Right
      { dx: 0, dy: 1 }, // Down
      { dx: -1, dy: 0 }, // Left
      { dx: 1, dy: -1 }, // Up-Right
      { dx: 1, dy: 1 }, // Down-Right
      { dx: -1, dy: 1 }, // Down-Left
      { dx: -1, dy: -1 }, // Up-Left
    ];

    for (const { dx, dy } of directions) {
      const nx = gx + dx;
      const ny = gy + dy;

      if (this.isValidTile(nx, ny)) {
        // For diagonal movement, ensure both adjacent cardinal tiles are walkable
        // to prevent corner cutting
        if (dx !== 0 && dy !== 0) {
          if (!this.isWalkable(gx + dx, gy) || !this.isWalkable(gx, gy + dy)) {
            continue;
          }
        }
        neighbors.push({ gx: nx, gy: ny });
      }
    }

    return neighbors;
  }

  /**
   * Get the grid as a 2D array for debugging/visualization.
   */
  toDebugArray(): TileType[][] {
    const grid: TileType[][] = [];
    for (let gy = 0; gy < GRID_HEIGHT; gy++) {
      const row: TileType[] = [];
      for (let gx = 0; gx < GRID_WIDTH; gx++) {
        row.push(this.getStaticTile(gx, gy));
      }
      grid.push(row);
    }
    return grid;
  }
}

// Singleton instance for the office navigation grid
let gridInstance: NavigationGrid | null = null;

export function getNavigationGrid(): NavigationGrid {
  if (!gridInstance) {
    gridInstance = new NavigationGrid();
  }
  return gridInstance;
}

export function resetNavigationGrid(): void {
  gridInstance = new NavigationGrid();
}

/**
 * Get obstacle tiles for debug visualization.
 * Returns array of {x, y, width, height, type} for each non-floor tile.
 */
export function getObstacleTiles(): Array<{
  x: number;
  y: number;
  width: number;
  height: number;
  type: TileType;
}> {
  const grid = getNavigationGrid();
  const obstacles: Array<{
    x: number;
    y: number;
    width: number;
    height: number;
    type: TileType;
  }> = [];

  for (let gy = 0; gy < GRID_HEIGHT; gy++) {
    for (let gx = 0; gx < GRID_WIDTH; gx++) {
      const type = grid.getStaticTile(gx, gy);
      if (type !== TileType.FLOOR && type !== TileType.ELEVATOR) {
        obstacles.push({
          x: gx * TILE_SIZE,
          y: gy * TILE_SIZE,
          width: TILE_SIZE,
          height: TILE_SIZE,
          type,
        });
      }
    }
  }

  return obstacles;
}
