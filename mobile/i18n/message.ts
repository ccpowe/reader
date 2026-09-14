import { i18n } from './index';

/** App-owned copy retains its identity while an error or notice is in state. */
export type LocalizedMessage = {
  key: string;
  values?: Record<string, string | number>;
};

export function localizedMessage(key: string, values?: LocalizedMessage['values']): LocalizedMessage {
  return values ? { key, values: { ...values } } : { key };
}

/** External/provider messages remain verbatim; only explicit descriptors translate. */
export function resolveMessage(message: string | LocalizedMessage): string {
  return typeof message === 'string' ? message : i18n.t(message.key, message.values);
}

export function bindLocalizedErrorMessage(
  error: Error,
  message: string | LocalizedMessage,
  transform: (value: string) => string = value => value,
): void {
  if (typeof message === 'string') return;
  Object.defineProperty(error, 'message', {
    configurable: true,
    enumerable: false,
    get: () => transform(resolveMessage(message)),
  });
}
