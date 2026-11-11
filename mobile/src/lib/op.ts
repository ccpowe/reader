/** A fresh id for one user operation, so its result can be matched to it (react-guidelines §2). */
let counter = 0;

export function newOp(): string {
  counter += 1;
  return `${Date.now().toString(36)}-${counter}`;
}
