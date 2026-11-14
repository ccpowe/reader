/** 登录 / 注册 against the connected server; success moves the session, and the route guard the page. */
import { useReducer } from 'react';

import { isApiError } from '@/lib/api/errors';
import { session, useSession } from '@/lib/app-session';
import { authProblemField, authReducer, initialAuth, localMessages } from '@/lib/auth-flow';
import { newOp } from '@/lib/op';
import { problemMessage } from '@/lib/user-error';
import { SignInView } from '@/screens/AuthViews';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

export default function SignIn() {
  useLanguage(); // texts follow the interface language
  const current = useSession();
  const [state, dispatch] = useReducer(authReducer, undefined, () => initialAuth());

  async function submit() {
    const op = newOp();
    const next = authReducer(state, { type: 'submit', op });
    dispatch({ type: 'submit', op });
    // Only a submit this reducer accepted sends a request (not a repeat while one is in flight).
    if (next.status !== 'submitting' || next.op !== op) return;
    try {
      const outcome =
        next.mode === 'sign-in'
          ? await session.signIn(next.email, next.password)
          : await session.register(next.email, next.password, next.displayName.trim() || null);
      // 'busy' / 'ignored': another attempt owns the session; this page waits for it.
      if (outcome !== 'done') dispatch({ type: 'failed', op, problem: { kind: 'aborted' } });
    } catch (error) {
      dispatch({ type: 'failed', op, problem: isApiError(error) ? error.problem : { kind: 'network' } });
    }
  }

  const problem = state.status === 'editing' ? state.problem : null;
  const field = authProblemField(problem);
  const message =
    problem === null || (typeof problem !== 'string' && problem.kind === 'aborted')
      ? null
      : typeof problem === 'string'
        ? localMessages[problem]
        : problem.kind === 'timeout'
          ? t().auth.signInTimeout // signing in is safe to repeat
          : problemMessage(problem);
  const server = current.status === 'signed-out' ? current.server : null;
  return (
    <SignInView
      displayName={state.displayName}
      email={state.email}
      emailError={field === 'email' ? message : null}
      expired={current.status === 'signed-out' && current.reason === 'expired'}
      formError={field === null ? message : null}
      mode={state.mode}
      onChange={(name, value) => dispatch({ type: 'edit', field: name, value })}
      onChangeServer={() => session.forgetServer()}
      onSubmit={() => void submit()}
      onSwitchMode={() => dispatch({ type: 'switch-mode' })}
      password={state.password}
      passwordError={field === 'password' ? message : null}
      serverLabel={server ? server.base.replace(/^https?:\/\//, '') : ''}
      submitting={state.status === 'submitting'}
    />
  );
}
