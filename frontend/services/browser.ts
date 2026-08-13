import { api } from "@/services/client";
import type {
  KaseTabResponse,
  KaseLinkResponse,
  KaseVerifyResponse,
} from "@/types/api";

/** Endpoints backed by the KASE browser agent.
 *
 *  These are on-demand checks (§28): the normal page render reads the
 *  database, and these are only called when the user explicitly asks to
 *  re-check a bond against the exchange.
 */
export const browserService = {
  verify: (identifier: string, options: { withVisual?: boolean } = {}) =>
    api.post<KaseVerifyResponse>(
      `/bonds/${encodeURIComponent(identifier)}/verify-on-kase`,
      { with_visual: options.withVisual ?? false },
    ),

  tab: (identifier: string, section: string) =>
    api.get<KaseTabResponse>(
      `/bonds/${encodeURIComponent(identifier)}/kase-tab/${encodeURIComponent(section)}`,
    ),

  link: (identifier: string) =>
    api.get<KaseLinkResponse>(
      `/bonds/${encodeURIComponent(identifier)}/kase-link`,
    ),
};
