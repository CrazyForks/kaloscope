<script lang="ts">
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import {
    Cell,
    DataView,
    FlowLogs,
    HCell,
    Image,
    Paginator,
    Search,
    Status,
    confirm,
    type PaginatorProps
  } from '$lib/components';
  import { createLoading, createSortField } from '$lib/helpers';
  import { _, dateTime, milliseconds } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { FlowGraph, Page, Resp } from '$lib/types';
  import { untrack } from 'svelte';

  let graphs: FlowGraph[] = $state([]);
  let graphName: string = $state('');
  let flowLogs: FlowLogs;

  const pagination: PaginatorProps = $state({ current: 1, size: 15, onchange: search });
  const ordering = createSortField();
  const loading = createLoading();

  /**
   * Search flow graphs.
   *
   * @param page - The page number.
   * @param size - The page size.
   */
  function search(page: number = 1, size: number = pagination.size) {
    loading.start();
    api
      .get('flow/graph/list', {
        searchParams: [
          ['page_num', page],
          ['page_size', size],
          ['ordering', $ordering],
          ['category', 'schedule'],
          ['states', 'modified'],
          ['states', 'published'],
          ['name', graphName]
        ]
      })
      .json<Resp<Page<FlowGraph>>>()
      .then((resp) => {
        pagination.current = page;
        pagination.size = size;
        pagination.total = resp.data.total;
        graphs = resp.data.items;
      })
      .finally(() => {
        loading.end();
      });
  }

  /**
   * Retract flow graph by ID.
   *
   * @param id - The flow graph ID.
   */
  function retract(id: number) {
    loading.start();
    api
      .post(`flow/graph/${id}/retract`)
      .then(() => search(pagination.current))
      .catch(() => loading.end());
  }

  $effect(() => {
    $ordering; // eslint-disable-line
    untrack(() => search());
  });
</script>

<DataView dvh loading={$loading} data={graphs}>
  {#snippet filters()}
    <Search label={$_('field.name')} bind:value={graphName} onsearch={() => search()} />
  {/snippet}
  {#snippet header()}
    <HCell width={['40%', '100%']} text={$_('field.name')} sort={ordering.bind('name')} />
    <HCell width={['30%', null]} text={$_('field.last_execution')} />
    <HCell width={['20%', null]} text={$_('field.average_time')} />
    <HCell width={['10%', '4rem']} text={$_('field.status')} class="justify-center" />
    <HCell actions />
  {/snippet}
  {#snippet row(graph)}
    <Cell>
      <Image transparent src={graph.icon} icon={icons.documentFlowchart} />
      <div class="truncate">
        <div class="mb-1 truncate" title={graph.name}>{graph.name}</div>
        <div class="truncate text-xs opacity-50" title={graph.description}>{graph.description}</div>
      </div>
    </Cell>
    <Cell text={$dateTime(graph.last_execution)} />
    <Cell text={$milliseconds(graph.average_time)} />
    <Cell class="justify-center">
      <Status rate={graph.success_rate} />
    </Cell>
    <Cell
      actions={[
        {
          disabled: !graph.last_execution,
          icon: icons.slideSearch,
          text: $_('action.view', $_('entity.logs')),
          onclick: () => flowLogs.showModal(graph.id)
        },
        {
          icon: icons.documentEdit,
          text: $_('action.edit', $_('entity.graph')),
          onclick: () => goto(`/settings/workflows/graphs/${graph.editable ? '' : 'r/'}${graph.id}`)
        },
        {
          icon: icons.back,
          text: $_('action.retract', $_('entity.graph')),
          onclick: () => {
            confirm({
              icon: icons.back,
              title: `${$_('action.retract', $_('entity.graph'))} [${graph.name}]`,
              onconfirm: () => retract(graph.id)
            });
          }
        }
      ]}
    />
  {/snippet}
  {#snippet paginator(disabled)}
    <Paginator {disabled} {...pagination} />
  {/snippet}
</DataView>

<FlowLogs bind:this={flowLogs} />
