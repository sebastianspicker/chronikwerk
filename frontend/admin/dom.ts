/** Typed DOM lookup helpers; templates remain the authoritative UI contract. */

export const qs = <T extends Element>(
  selector: string,
  parent: ParentNode = document,
): T | null => parent.querySelector<T>(selector);

export const qsa = <T extends Element>(
  selector: string,
  parent: ParentNode = document,
): T[] => [...parent.querySelectorAll<T>(selector)];
