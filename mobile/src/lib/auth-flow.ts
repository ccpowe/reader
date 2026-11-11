/**
 * The sign-in / register page as a state machine (react-guidelines §2).
 *
 *   editing ──submit(op)──▶ submitting(op) ──failed(op, error)──▶ editing + error
 *
 * Success is not an event: the session becomes signed-in and the route guard replaces this page.
 * Checks that need no server (empty email, short password) happen on submit.
 * Pure: runs under `node --test`.
 */
import type { ApiProblem } from './api/errors.ts';

export type AuthMode = 'sign-in' | 'register';

type Fields = { mode: AuthMode; email: string; password: string; displayName: string };

export type LocalProblem =
  | 'email-empty'
  | 'email-invalid'
  | 'password-empty'
  | 'password-short'
  /** the register request got no answer: the account may exist (react-guidelines §5) */
  | 'register-unknown';

export type AuthState =
  | (Fields & { status: 'editing'; problem: LocalProblem | ApiProblem | null })
  | (Fields & { status: 'submitting'; op: string });

export type AuthEvent =
  | { type: 'edit'; field: 'email' | 'password' | 'displayName'; value: string }
  | { type: 'switch-mode' }
  | { type: 'submit'; op: string }
  | { type: 'failed'; op: string; problem: ApiProblem };

export const MIN_PASSWORD = 8; // RegisterRequest.password minLength

export function initialAuth(email = ''): AuthState {
  return { status: 'editing', mode: 'sign-in', email, password: '', displayName: '', problem: null };
}

function check(fields: Fields): LocalProblem | null {
  const email = fields.email.trim();
  if (!email) return 'email-empty';
  if (!/^[^\s@]+@[^\s@]+$/.test(email)) return 'email-invalid';
  if (!fields.password) return 'password-empty';
  if (fields.mode === 'register' && fields.password.length < MIN_PASSWORD) return 'password-short';
  return null;
}

export function authReducer(state: AuthState, event: AuthEvent): AuthState {
  switch (state.status) {
    case 'editing':
      if (event.type === 'edit') return { ...state, [event.field]: event.value, problem: null };
      if (event.type === 'switch-mode') return { ...state, mode: state.mode === 'sign-in' ? 'register' : 'sign-in', problem: null };
      if (event.type === 'submit') {
        const problem = check(state);
        if (problem) return { ...state, problem };
        const { mode, email, password, displayName } = state;
        return { status: 'submitting', mode, email: email.trim(), password, displayName, op: event.op };
      }
      return state;
    case 'submitting':
      if (event.type === 'failed' && event.op === state.op) {
        const { mode, email, password, displayName } = state;
        // No answer to a register request: the account may have been created. Say so, and offer
        // signing in with what was typed instead of a second registration.
        const unknown = event.problem.kind === 'network' || event.problem.kind === 'timeout';
        if (mode === 'register' && unknown) {
          return { status: 'editing', mode: 'sign-in', email, password, displayName, problem: 'register-unknown' };
        }
        // Keep what was typed, except a password the server said is wrong.
        const wrongPassword = event.problem.kind === 'http' && event.problem.code === 'invalid_credentials';
        return { status: 'editing', mode, email, password: wrongPassword ? '' : password, displayName, problem: event.problem };
      }
      return state;
  }
}

export const localMessages: Record<LocalProblem, string> = {
  'email-empty': '请输入邮箱。',
  'email-invalid': '邮箱格式不对。',
  'password-empty': '请输入密码。',
  'password-short': `密码至少 ${MIN_PASSWORD} 个字符。`,
  'register-unknown': '没收到服务器的回应，账号可能已经创建。请直接登录试试；登录不上再重新注册。',
};

/** Which input a problem belongs to; the rest show above the button. */
export function authProblemField(problem: LocalProblem | ApiProblem | null): 'email' | 'password' | null {
  if (problem === null) return null;
  if (problem === 'register-unknown') return null;
  if (typeof problem === 'string') return problem.startsWith('email') ? 'email' : 'password';
  if (problem.kind === 'http' && problem.code === 'invalid_credentials') return 'password';
  if (problem.kind === 'http' && problem.code === 'email_taken') return 'email';
  return null;
}
