import { api } from '$lib/api';
import type { IndexerConfig, Resp } from '$lib/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  // get the indexer config
  let config: IndexerConfig = {};
  await api
    .get(`flow/indexer/${params.indexer_id}/config`)
    .json<Resp<IndexerConfig>>()
    .then((resp) => (config = resp.data));

  return { config };
};
