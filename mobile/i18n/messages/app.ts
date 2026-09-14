export const zhCN = {
  serverTokenInvalid: "服务器访问令牌无效，请重新输入。",

  cancelConnection: '取消连接',
  confirmConnection: '确认并连接',
  identityMessage: '已保存服务器「{{previous}}」，但发现了身份不同的服务器「{{next}}」。只有在你确认信任新身份时才会恢复登录状态。',
  identityNote: '已保存 API 地址：{{previous}}\n发现 API 地址：{{next}}',
  confirmIdentity: '确认新的服务器身份',
  restoringSession: '正在恢复登录状态…',
};

export const en = {
  serverTokenInvalid: "The server access token is invalid. Enter it again.",

  cancelConnection: 'Cancel connection',
  confirmConnection: 'Confirm and connect',
  identityMessage: 'The saved server is “{{previous}}”, but a different server identity, “{{next}}”, was found. Your session will only be restored if you confirm that you trust the new identity.',
  identityNote: 'Saved API address: {{previous}}\nDiscovered API address: {{next}}',
  confirmIdentity: 'Confirm new server identity',
  restoringSession: 'Restoring your session…',
} satisfies Record<keyof typeof zhCN, string>;
