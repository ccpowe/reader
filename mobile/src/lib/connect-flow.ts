/**
 * The connect page as a state machine (react-guidelines §2).
 *
 *   editing ──submit(op)──▶ checking(op) ──checked(op, ok)──▶ confirming ──back──▶ editing
 *                                        └─checked(op, problem)──▶ editing + problem
 *
 * "Confirm" is not an event here: the page hands the confirmed server to the session, which
 * replaces this page (route guard). A result of an older check (`op` mismatch) is ignored.
 * Pure: runs under `node --test`.
 */
import type { Discovered, DiscoveryProblem, DiscoveryResult } from './connection/discovery.ts';

type Fields = { address: string; token: string };

export type ConnectState =
  | (Fields & { status: 'editing'; problem: DiscoveryProblem | null; notice: 'token-rejected' | null })
  | (Fields & { status: 'checking'; op: string })
  | (Fields & { status: 'confirming'; server: Discovered });

export type ConnectEvent =
  | { type: 'edit'; field: keyof Fields; value: string }
  | { type: 'submit'; op: string }
  | { type: 'checked'; op: string; result: DiscoveryResult }
  | { type: 'back' };

/** Opening the page: prefilled after the server rejected the token (session `no-server`). */
export function initialConnect(lastBase?: string, tokenRejected?: boolean): ConnectState {
  return { status: 'editing', address: lastBase ?? '', token: '', problem: null, notice: tokenRejected ? 'token-rejected' : null };
}

export function connectReducer(state: ConnectState, event: ConnectEvent): ConnectState {
  switch (state.status) {
    case 'editing':
      if (event.type === 'edit') return { ...state, [event.field]: event.value, problem: null };
      if (event.type === 'submit') {
        if (!state.address.trim()) return { ...state, problem: { kind: 'address', problem: 'empty' } };
        if (!state.token.trim()) return { ...state, problem: { kind: 'wrong-token' } };
        return { status: 'checking', address: state.address, token: state.token, op: event.op };
      }
      return state;
    case 'checking':
      if (event.type === 'checked' && event.op === state.op) {
        return event.result.ok
          ? { status: 'confirming', address: state.address, token: state.token, server: event.result.server }
          : { status: 'editing', address: state.address, token: state.token, problem: event.result.problem, notice: null };
      }
      return state;
    case 'confirming':
      if (event.type === 'back') return { status: 'editing', address: state.address, token: state.token, problem: null, notice: null };
      return state;
  }
}

/** Which input a problem belongs to, so the page shows it under that field. */
export function problemField(problem: DiscoveryProblem | null): 'address' | 'token' | null {
  if (!problem) return null;
  return problem.kind === 'wrong-token' ? 'token' : 'address';
}
