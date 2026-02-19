<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api } from '$lib/api';
  import {
    Badge,
    Button,
    Cell,
    Checkbox,
    DataView,
    FlowLogs,
    GraphBasics,
    GraphEditor,
    HCell,
    Paginator,
    Search,
    Select,
    Status,
    alert,
    confirm,
    type PaginatorProps
  } from '$lib/components';
  import { GraphCategory, GraphState, enumToOptions } from '$lib/enums';
  import { createLoading, createSortField } from '$lib/helpers';
  import { _, dateTime, milliseconds } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { FlowGraph, OptionValue, Page, Resp } from '$lib/types';
  import { untrack } from 'svelte';

  let graphs: FlowGraph[] = $state([]);
  let graphName: string = $state('');
  let graphState: OptionValue = $state(null);
  let graphCategory: OptionValue = $state(null);
  let graphEditor: GraphEditor;
  let headerCheckbox: Checkbox;
  let zipInput: HTMLInputElement;
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
        searchParams: {
          page_num: page,
          page_size: size,
          ordering: $ordering,
          category: graphCategory ?? '',
          state: graphState ?? '',
          name: graphName
        }
      })
      .json<Resp<Page<FlowGraph>>>()
      .then((resp) => {
        pagination.current = page;
        pagination.size = size;
        pagination.total = resp.data.total;
        graphs = resp.data.list;
      })
      .finally(() => {
        loading.end();
        headerCheckbox.unselectAll();
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

  /**
   * Delete flow graph by ID.
   *
   * @param id - The flow graph ID.
   */
  function del(id: number) {
    loading.start();
    api
      .post('flow/graph/delete', { json: { ids: [id] } })
      .then(() => search(pagination.current))
      .catch(() => loading.end());
  }

  /**
   * Export the flow graphs as a zip file.
   */
  function exportGraphs() {
    const selectedKeys = headerCheckbox.getSelectedKeys();
    if (selectedKeys.length === 0) {
      alert({ message: 'select_export_data' });
      return;
    }
    loading.start();
    api
      .post('flow/graph/export', { json: { ids: selectedKeys } })
      .blob()
      .then((blob) => {
        if (blob.size === 0) {
          alert({ level: 'error', message: 'export_graphs_failed' });
          return;
        }
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${new Date().getTime()}.zip`;
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      })
      .finally(() => loading.end());
  }

  /**
   * Import the flow graphs from a zip file.
   */
  function importGraphs() {
    const zip = zipInput.files?.[0];
    if (!zip) {
      return;
    }
    const formData = new FormData();
    formData.append('zip', zip);
    zipInput.value = '';
    loading.start();
    api
      .post('flow/graph/import', { body: formData })
      .then(() => {
        graphName = '';
        graphState = null;
        graphCategory = null;
        search();
      })
      .catch(() => loading.end());
  }

  $effect(() => {
    $ordering; // eslint-disable-line
    untrack(() => search());
  });
</script>

<DataView dvh loading={$loading} data={graphs}>
  {#snippet filters()}
    <Select
      filter
      options={enumToOptions(GraphCategory)}
      bind:value={graphCategory}
      label={$_('model.field.category')}
      onchange={() => search()}
      class="max-md:hidden"
    />
    <Select
      filter
      options={enumToOptions(GraphState)}
      bind:value={graphState}
      label={$_('model.field.state')}
      onchange={() => search()}
      class="max-lg:hidden"
    />
    <Search label={$_('model.field.name')} bind:value={graphName} onsearch={() => search()} />
  {/snippet}
  {#snippet actions()}
    <Button
      size="md"
      icon={icons.boxArrowUp}
      text={$_('action.export', $_('model.graphs'))}
      onclick={() => exportGraphs()}
    />
    <Button
      size="md"
      icon={icons.layerDiagonalAdd}
      text={$_('action.import', $_('model.graphs'))}
      onclick={() => zipInput.click()}
    />
    <input type="file" class="hidden" accept="application/zip" bind:this={zipInput} onchange={importGraphs} />
    <Button
      size="md"
      icon={icons.documentAdd}
      text={$_('action.add', $_('model.graph'))}
      onclick={() => graphEditor.showModal()}
    />
  {/snippet}
  {#snippet header()}
    <HCell width="2rem">
      <Checkbox batch={graphs.filter((g) => g.state !== 'drafting').length} bind:this={headerCheckbox} />
    </HCell>
    <HCell width={['35%', '55%']} text={$_('model.field.name')} sort={ordering.bind('name')} />
    <HCell width={['15%', '45%']} text={$_('model.field.category')} sort={ordering.bind('category')} />
    <HCell width={['20%', null]} text={$_('model.field.updated')} sort={ordering.bind('updated_at')} />
    <HCell width={['15%', null]} text={$_('model.field.average_time')} />
    <HCell width={['15%', '4rem']} text={$_('model.field.state')} sort={ordering.bind('state')} />
    <HCell actions />
  {/snippet}
  {#snippet row(graph)}
    {@const drafting = graph.state === 'drafting'}
    <Cell>
      <Checkbox key={String(graph.id)} disabled={drafting} />
    </Cell>
    <Cell>
      <GraphBasics {graph} imgClass="max-sm:hidden" />
    </Cell>
    <Cell>
      {@const category = GraphCategory[graph.category]}
      <Badge icon={category.icon} iconColor={category.iconColor}>{$_(category.label)}</Badge>
    </Cell>
    <Cell text={$dateTime(graph.updated_at)} />
    <Cell class="max-lg:hidden">
      <Status rate={graph.success_rate} />
      {$milliseconds(graph.average_time)}
    </Cell>
    <Cell class="max-lg:pl-3">
      {@const state = GraphState[graph.state]}
      <Badge icon={state.icon} iconColor={state.iconColor} dashed={drafting}>
        <span class="max-lg:hidden">{$_(state.label)}</span>
      </Badge>
    </Cell>
    <Cell
      actions={[
        {
          icon: icons.documentEdit,
          text: $_('action.edit', $_('model.graph')),
          onclick: () => goto(`/workflows/graphs/${graph.editable ? '' : 'r/'}${graph.id}`)
        },
        {
          condition: !drafting,
          icon: icons.back,
          text: $_('action.retract', $_('model.graph')),
          onclick: () => {
            confirm({
              icon: icons.back,
              title: `${$_('action.retract', $_('model.graph'))} [${graph.name}]`,
              onconfirm: () => retract(graph.id)
            });
          }
        },
        {
          condition: drafting,
          icon: icons.deleteDismiss,
          text: $_('action.delete', $_('model.graph')),
          onclick: () => {
            confirm({
              icon: icons.deleteDismiss,
              title: `${$_('action.delete', $_('model.graph'))} [${graph.name}]`,
              onconfirm: () => del(graph.id)
            });
          }
        },
        {
          condition: !!graph.last_execution,
          icon: icons.slideSearch,
          text: $_('action.view', $_('model.logs')),
          onclick: () => flowLogs.showModal(graph.id)
        }
      ]}
    />
  {/snippet}
  {#snippet paginator(disabled)}
    <Paginator {disabled} {...pagination} />
  {/snippet}
</DataView>

<GraphEditor bind:this={graphEditor} onsave={(result) => goto(`${page.url.pathname}/${result.id}`)} />

<FlowLogs bind:this={flowLogs} />
