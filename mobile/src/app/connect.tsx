/** 连接服务器: discovery, then the user confirms the server and the session takes it. */
import { useReducer } from 'react';

import { session, useSession } from '@/lib/app-session';
import { connectReducer, initialConnect, problemField, type ConnectState } from '@/lib/connect-flow';
import { discover } from '@/lib/connection/discovery';
import { newOp } from '@/lib/op';
import { discoveryMessage } from '@/lib/user-error';
import { ConnectView } from '@/screens/AuthViews';

export default function Connect() {
  const current = useSession();
  const [state, dispatch] = useReducer(connectReducer, undefined, () =>
    current.status === 'no-server' ? initialConnect(current.lastBase, current.tokenRejected) : initialConnect(),
  );

  async function submit() {
    const op = newOp();
    const next = connectReducer(state, { type: 'submit', op });
    dispatch({ type: 'submit', op });
    if (next.status !== 'checking' || next.op !== op) return;
    const result = await discover(next.address, next.token.trim());
    dispatch({ type: 'checked', op, result });
  }

  function confirm(confirming: Extract<ConnectState, { status: 'confirming' }>) {
    const { server, token } = confirming;
    session.connect({ base: server.base, apiBaseUrl: server.apiBaseUrl, serverId: server.serverId, serverToken: token.trim() });
  }

  const problem = state.status === 'editing' ? state.problem : null;
  const field = problemField(problem);
  return (
    <ConnectView
      address={state.address}
      addressError={field === 'address' && problem ? discoveryMessage(problem) : null}
      notice={state.status === 'editing' && state.notice === 'token-rejected' ? '服务器口令已更换，请输入新的口令。' : null}
      onBack={() => dispatch({ type: 'back' })}
      onChange={(name, value) => dispatch({ type: 'edit', field: name, value })}
      onConfirm={() => state.status === 'confirming' && confirm(state)}
      onSubmit={() => void submit()}
      server={state.status === 'confirming' ? state.server : null}
      status={state.status}
      token={state.token}
      tokenError={field === 'token' && problem ? (state.token.trim() ? discoveryMessage(problem) : '请输入服务器口令。') : null}
    />
  );
}
