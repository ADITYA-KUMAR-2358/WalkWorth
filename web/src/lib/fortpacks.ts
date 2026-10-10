// Fort packs are bundled into the app so Trail mode works with zero signal.
import sinhagad from "../../../fortpacks/sinhagad.json";
import type { FortPack } from "../types/schema";

export const fortPacks: FortPack[] = [sinhagad as FortPack];

export function fortPack(id: string | null | undefined): FortPack | undefined {
  return fortPacks.find((p) => p.id === id);
}
