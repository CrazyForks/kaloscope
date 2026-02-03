import { goto } from '$app/navigation';
import { alert } from '$lib/components';
import { token } from '$lib/stores';
import type { BaseResp } from '$lib/types';
import ky from 'ky';
import { get } from 'svelte/store';

/**
 * The API client for the application.
 */
export const api = ky.create({
  prefixUrl: '/_api',
  timeout: 60000,
  hooks: {
    beforeRequest: [
      (request) => {
        const _token = get(token);
        if (_token) {
          // authorization tokens in the form `Token <token>` or `Bearer <token>` are supported
          // see https://sanic.dev/en/guide/basics/headers.html for more details
          request.headers.set('Authorization', `Token ${_token}`);
        }
      }
    ],
    afterResponse: [
      async (request, options, response) => {
        if (response.ok && response.headers.get('Content-Type') === 'application/json') {
          const resp = await response.json<BaseResp>();
          if (resp.message) {
            alert({ level: 'success', message: resp.message });
          }
        }
      }
    ],
    beforeError: [
      async (error) => {
        const resp = await error.response.json<BaseResp>();

        // goto login page if unauthorized
        if (resp.status === 401) {
          token.set(null);
          goto('/login');
        }

        // alert error message
        error.message = resp.message;
        if (resp.message || resp.status === 500) {
          alert({
            level: 'error',
            message: resp.message || 'internal_server_error',
            unique: resp.status === 401
          });
          return error;
        }

        // log other errors
        console.error(error);
        return error;
      }
    ]
  }
});

/**
 * Proxies the image URL through the server.
 *
 * @param url - The original URL of the image.
 * @param policy - The policy for proxying the image.
 * @returns The proxied URL of the image.
 */
export function proxyImage(url: string | null, policy: boolean | 'store' = false): string | null {
  if (url) {
    if (url.startsWith('icons/') || url.startsWith('avatars/')) {
      // serve icons and avatars directly from the API endpoint
      return `/_api/${url}`;
    } else if (policy !== false) {
      // proxy images through the `/image/proxy` endpoint
      return `/_api/image/proxy?store=${policy === 'store'}&url=${encodeURIComponent(url)}`;
    }
  }
  // return the original URL
  return url;
}
