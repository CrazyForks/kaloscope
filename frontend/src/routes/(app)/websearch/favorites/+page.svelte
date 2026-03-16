<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api } from '$lib/api';
  import { Cell, DataView, HCell, Image, Paginator, Uploader, confirm, type PaginatorProps } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _, dateTime } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { restorePosition } from '$lib/stores';
  import type { Favorite, Page, Resp } from '$lib/types';
  import { onMount } from 'svelte';
  import { queryParameters, ssp } from 'sveltekit-search-params';

  let favorites: Favorite[] = $state([]);

  // the URL query parameters
  const query = queryParameters(
    {
      page_num: ssp.number(0),
      page_size: ssp.number(0)
    },
    {
      pushHistory: false
    }
  );

  let pagination: Omit<PaginatorProps, 'current' | 'size'> = $state({ onchange: () => search(true) });
  const loading = createLoading();

  /**
   * Search for favorites.
   *
   * @param toTop - Whether to scroll to the top of the page after the search.
   */
  function search(toTop: boolean = false) {
    loading.start();
    api
      .post('user/favorites', {
        json: {
          page_num: query.page_num,
          page_size: query.page_size
        }
      })
      .json<Resp<Page<Favorite>>>()
      .then((resp) => {
        pagination.total = resp.data.total;
        favorites = resp.data.items;
      })
      .finally(() => {
        loading.end();
        restorePosition(window, toTop);
      });
  }

  /**
   * Delete a favorite resource.
   *
   * @param favorite - The favorite resource.
   */
  function del(favorite: Favorite) {
    loading.start();
    api
      .post(`flow/indexer/${favorite.indexer_id}/unfavorite`, {
        json: {
          rsrc_id: favorite.rsrc.id
        }
      })
      .then(() => search())
      .catch(() => loading.end());
  }

  onMount(() => {
    const params = page.url.searchParams;
    query.page_num = Number(params.get('page_num')) || 1;
    query.page_size = Number(params.get('page_size')) || 15;
    search();
  });
</script>

<DataView loading={$loading} data={favorites}>
  {#snippet header()}
    <HCell width="100%" text={$_('entity.favorites')} />
    <HCell actions />
  {/snippet}
  {#snippet row(favorite)}
    {@const rsrc = favorite.rsrc}
    <Cell class="group {favorite.url ? 'cursor-pointer' : ''}" onclick={() => favorite.url && goto(favorite.url)}>
      <Image transparent src={rsrc.cover} width="3.5rem" />
      <div class="flex w-full flex-col gap-4">
        <div class={favorite.url ? 'transition-colors group-hover:text-primary' : ''}>
          <span class="text-sm">{rsrc.title}</span>
          {#if rsrc.misc}
            <span class="italic-text">[{rsrc.misc}]</span>
          {/if}
        </div>
        <Uploader up={rsrc.uploader} extra={$dateTime(favorite.created_at)} />
      </div>
    </Cell>
    <Cell
      actions={[
        {
          icon: icons.deleteDismiss,
          text: $_('action.delete', $_('entity.favorite')),
          onclick: () => {
            confirm({
              icon: icons.deleteDismiss,
              title: $_('action.delete', $_('entity.favorite')),
              onconfirm: () => del(favorite)
            });
          }
        }
      ]}
    />
  {/snippet}
  {#snippet paginator(disabled)}
    <Paginator {disabled} {...pagination} bind:current={query.page_num} size={query.page_size} />
  {/snippet}
</DataView>
