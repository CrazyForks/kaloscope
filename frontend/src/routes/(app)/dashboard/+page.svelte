<script lang="ts">
  import { api } from '$lib/api';
  import { Container, DataView, HCell, Image, SearchHit, ViewSwitcher } from '$lib/components';
  import { restorePosition } from '$lib/stores';
  import type { FlowGraph, IndexerAuth, IndexerConfig, Resource, Resp, ViewMode, ViewModes } from '$lib/types';
  import { onMount } from 'svelte';
  import { fade } from 'svelte/transition';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  type Resources = {
    title?: string | null;
    items: Resource[];
  };
  type Board = {
    id: number;
    name: string;
    icon: string | null;
    loading: boolean | null;
    activeId: number | null;
    resources: Resources[];
    coverRatio: string;
    viewModes: ViewModes;
    viewMode: ViewMode;
    config: IndexerConfig;
  };
  let boards: Record<number, Board> = $state({});

  /**
   * Load board resources.
   *
   * @param graph - The flow graph instance.
   */
  async function load(graph: FlowGraph) {
    const { id, name, icon } = graph;
    const reload = !!boards[id];
    let board = reload ? boards[id] : ({} as Board);
    try {
      if (!reload) {
        // load the indexer config
        const config = (await api.get(`flow/indexer/${id}/config`).json<Resp<IndexerConfig>>()).data;
        const { login } = config.auth ?? {};
        const { display } = config.board ?? {};
        // check if login is required
        if (login?.required) {
          const resp = await api.get(`flow/indexer/${id}/auth`).json<Resp<IndexerAuth>>();
          if (!resp.data || resp.data.name === null || resp.data.name === undefined) {
            return;
          }
        }
        // create a new board object
        const modes = display?.view_modes ?? [];
        const viewModes = (Array.isArray(modes) && modes.length > 0 ? modes : ['table']) as ViewModes;
        board = {
          id: id,
          name: name,
          icon: icon,
          loading: null,
          activeId: 0,
          resources: [],
          coverRatio: display?.cover_ratio ?? '16/9',
          viewModes: viewModes,
          viewMode: viewModes[0],
          config: config
        };
        boards[id] = board;
      }
      // set the loading state
      boards[id].loading = false;
      setTimeout(() => {
        if (boards[id] && boards[id].loading === false) {
          boards[id].loading = true;
        }
      }, 500);
      // load the resources
      const resp = await api
        .post(`flow/graph/${id}/execute`, { json: { $start: 'board_start' } })
        .json<Resp<Resources[]>>();
      board.resources = resp.data || [];
    } catch (error) {
      console.error(error);
      board.resources = [];
    } finally {
      if (boards[id]) {
        boards[id] = board;
        boards[id].loading = null;
      }
    }
  }

  onMount(async () => {
    // clear the results
    boards = {};
    for (const graph of data.graphs) {
      await load(graph);
    }
    restorePosition(window);
  });
</script>

<Container padding="1rem 0" maxWidth="max(150vh,40rem)">
  {#each Object.values(boards) as board (board.id)}
    <div class="flex h-8 items-center justify-between px-4" transition:fade={{ duration: 200 }}>
      <span class="flex-center gap-2 text-xl font-bold opacity-80">
        {#if board.icon}
          <Image src={board.icon} width="1.5rem" />
        {/if}
        {board.name}
      </span>
      {#if board.loading}
        <span class="loading loading-sm loading-spinner"></span>
      {:else if board.resources.length > 0}
        <ViewSwitcher modes={board.viewModes} bind:mode={board.viewMode} />
      {/if}
    </div>
    <div class="divider my-0"></div>
    {#if board.resources.length > 0}
      <div class="tabs-border mb-10 tabs" transition:fade>
        {#each board.resources as rsrcs, index (index)}
          <label class="tab {board.resources.length === 1 ? 'hidden' : ''}">
            <input type="radio" checked={board.activeId === index} onclick={() => (board.activeId = index)} />
            <span class="font-semibold {board.activeId === index ? 'text-shadow-xs' : ''}">{rsrcs.title}</span>
          </label>
          {#if board.activeId === index}
            <div class="tab-content mt-2" transition:fade>
              <DataView
                data={rsrcs.items}
                mode={board.viewMode}
                loading={board.loading}
                class="px-0!"
                tableClass="[&_thead_tr]:border-0"
                gridClass="grid-cols-2 grid-cols-sparse"
                itemClass="rounded-sm bg-base-100 shadow-sm lg:hover:shadow-lg lg:mb-4"
              >
                <!-- table view -->
                {#snippet header()}
                  <HCell width="100%" />
                  <HCell width="3rem" />
                {/snippet}
                {#snippet row(rsrc)}
                  <SearchHit
                    mode="table"
                    {rsrc}
                    searchButton
                    indexerId={board.id}
                    indexerConfig={board.config}
                    coverRatio={board.coverRatio}
                  />
                {/snippet}

                <!-- grid view -->
                {#snippet item(rsrc)}
                  <SearchHit
                    mode="grid"
                    {rsrc}
                    searchButton
                    indexerId={board.id}
                    indexerConfig={board.config}
                    coverRatio={board.coverRatio}
                  />
                {/snippet}
              </DataView>
            </div>
          {/if}
        {/each}
      </div>
    {/if}
  {/each}
</Container>
