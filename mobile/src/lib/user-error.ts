/**
 * Errors as text people can act on (react-guidelines §5). Every message says what happened and,
 * when there is one, what to do; none shows a raw code. Chinese only until the interface language
 * setting (block 7) brings i18n.
 * Pure: runs under `node --test`.
 */
import { isApiError, type ApiProblem } from './api/errors.ts';
import type { DiscoveryProblem } from './connection/discovery.ts';
import type { ServerUrlProblem } from './connection/url.ts';

const address: Record<ServerUrlProblem, string> = {
  empty: '请输入服务器地址。',
  whitespace: '地址里不能有空格。',
  invalid: '这不是一个有效的地址。',
  protocol: '地址要以 http:// 或 https:// 开头。',
  credentials: '地址里不能带用户名或密码。',
  query: '地址里不能带「?」后面的参数。',
  fragment: '地址里不能带「#」后面的部分。',
  port: '端口号不对。',
};

function seconds(value: number | undefined) {
  return value && value > 0 ? `约 ${value < 60 ? `${Math.ceil(value)} 秒` : `${Math.ceil(value / 60)} 分钟`}后再试。` : '请稍后再试。';
}

export function discoveryMessage(problem: DiscoveryProblem): string {
  switch (problem.kind) {
    case 'address':
      return address[problem.problem];
    case 'unreachable':
      return '连不上这个地址。检查地址是否正确、服务器是否在运行，以及手机能否访问它。';
    case 'timeout':
      return '服务器没有响应。检查网络后再试。';
    case 'not-reader':
      return '这个地址上不是 Reader 服务器。';
    case 'old-protocol':
      return '服务器和 App 的版本不匹配，需要更新服务器或 App。';
    case 'redirected':
      return `这个地址会跳转到 ${problem.to}。请直接填写跳转后的地址。`;
    case 'insecure-api':
      return '服务器要求改用不加密的 http 连接，为了安全已拒绝。请检查服务器的公开地址设置。';
    case 'wrong-token':
      return '服务器口令不对。';
    case 'not-configured':
      return '服务器还没配置好，请检查它的设置。';
    case 'rate-limited':
      return `尝试次数过多，${seconds(problem.retryAfter)}`;
  }
}

/** Messages for specific error codes; anything else falls back to the generic ones below. */
const codes: Record<string, string> = {
  invalid_credentials: '邮箱或密码不对。',
  email_taken: '这个邮箱已经注册过，可以直接登录。',
  invalid_server_token: '服务器口令已更换，请重新连接。',
  not_authenticated: '登录已失效，请重新登录。',
  server_not_configured: '服务器还没配置好，请检查它的设置。',
  database_unavailable: '服务器暂时无法访问数据库，请稍后再试。',
  invalid_rss: '这不是一个有效的 RSS / Atom 地址。',
  invalid_website: '这不是一个有效的网址。',
  invalid_youtube: '认不出这个频道。可以填频道链接、@handle 或频道 ID。',
  invalid_x: '认不出这个账号。可以填 @handle 或 x.com 链接。',
  invalid_reddit: '认不出这个社区。可以填 r/python 或社区链接。',
  unsafe_url: '这个地址指向内网或本机，为了安全不能订阅。',
  feed_not_found: '这个网站没有找到 RSS / Atom。可以试试直接填它的 RSS 地址。',
  source_unreachable: '连不上这个地址。检查地址是否正确，稍后再试。',
  subscription_not_found: '这个订阅已经不存在了。',
  wrong_password: '当前密码不对。',
  avatar_too_large: '图片太大了，最大 5 MB。',
  unsupported_avatar_type: '只支持 JPEG、PNG 或 WebP 图片。',
  invalid_avatar: '这张图片读不出来，换一张试试。',
  unknown_engine: '服务器没有这个翻译引擎。',
  engine_unavailable: '这个翻译引擎现在不可用。',
  translation_unavailable: '服务器暂时不能翻译，请稍后再试。',
};

const fields: Record<string, string> = {
  email: '邮箱格式不对。',
  password: '密码需要 8 到 128 个字符。',
  display_name: '名字太长了。',
};

export function problemMessage(problem: ApiProblem): string {
  switch (problem.kind) {
    case 'network':
      return '连不上服务器。检查网络后再试。';
    case 'timeout':
      return '服务器没有及时响应，操作结果未知。稍后刷新看看。';
    case 'aborted':
      return '已取消。';
    case 'invalid-response':
      return problem.status >= 500 ? '服务器暂时出错了，请稍后再试。' : '服务器的回应无法识别。';
    case 'http': {
      if (problem.status === 429) return `操作太频繁，${seconds(problem.retryAfter)}`;
      if (problem.code === 'invalid_request' && problem.fields?.length) {
        const field = problem.fields.map((f) => f.field.split('.').pop() ?? '').find((name) => fields[name]);
        if (field) return fields[field];
      }
      if (codes[problem.code]) return codes[problem.code];
      if (problem.status >= 500) return '服务器暂时出错了，请稍后再试。';
      return '操作没有成功，请稍后再试。';
    }
  }
}

/** Any thrown value as one sentence. */
export function errorMessage(error: unknown): string {
  return isApiError(error) ? problemMessage(error.problem) : '出了点问题，请稍后再试。';
}
