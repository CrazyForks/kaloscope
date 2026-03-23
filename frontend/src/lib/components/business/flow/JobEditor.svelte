<script lang="ts" module>
  import type { FlowGraph, FlowJob, Option, Page, Resp } from '$lib/types';
  import { enumToOptions, IntervalUnit, JobTrigger } from '$lib/enums';

  type JobEditorProps = Partial<{
    id: number;
    graph_id: number;
    trigger: keyof typeof JobTrigger;
    bootparams: string;
    run_date: string | null;
    cron_expr: string | null;
    interval_num: number | null;
    interval_unit: keyof typeof IntervalUnit | null;
    interval_start: string | null;
    interval_end: string | null;
    onsave: (result: FlowJob) => void;
  }>;
</script>

<script lang="ts">
  import { enhance } from '$app/forms';
  import { api } from '$lib/api';
  import { CodeMirror, Label, Modal, Select, alert } from '$lib/components';
  import { createFormSchema, createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { json } from '@codemirror/lang-json';
  import { onMount } from 'svelte';

  let {
    id,
    graph_id,
    trigger = 'cron',
    bootparams = '{}',
    run_date = null,
    cron_expr = null,
    interval_num = null,
    interval_unit = null,
    interval_start = null,
    interval_end = null,
    onsave
  }: JobEditorProps = $props();

  // the modal dialog instance
  let modal: Modal;
  export const showModal = () => modal.show();

  // graph options for the dropdown
  let graphOptions: Option[] = $state([]);

  // the loading state and form schema
  const loading = createLoading();
  const schema = createFormSchema(({ datetime, text, number }) => ({
    run_date: datetime(),
    cron_expr: text().maxlength(255).pattern('\\S+(\\s+\\S+){4}'),
    interval_num: number().min(1),
    interval_start: datetime().required(false),
    interval_end: datetime().required(false)
  }));

  /**
   * Save or update the flow job.
   */
  function upsert() {
    loading.start();
    const jsonData: Record<string, unknown> = { id, graph_id, trigger };
    try {
      jsonData.bootparams = JSON.parse(bootparams);
    } catch {
      alert({ level: 'error', message: 'invalid_boot_params' });
      loading.end();
      return;
    }
    if (trigger === 'date') {
      jsonData.run_date = run_date || null;
    } else if (trigger === 'cron') {
      jsonData.cron_expr = cron_expr;
    } else if (trigger === 'interval') {
      jsonData.interval_num = interval_num;
      jsonData.interval_unit = interval_unit;
      jsonData.interval_start = interval_start || null;
      jsonData.interval_end = interval_end || null;
    }
    api
      .post('flow/job/upsert', { json: jsonData })
      .json<Resp<FlowJob>>()
      .then((resp) => {
        modal.close();
        onsave?.(resp.data);
        setTimeout(() => {
          trigger = 'cron';
          bootparams = '{}';
          run_date = null;
          cron_expr = null;
          interval_num = null;
          interval_unit = null;
          interval_start = null;
          interval_end = null;
        }, 200);
      })
      .finally(() => loading.end());
  }

  onMount(() => {
    // load the available graphs for scheduling
    api
      .get('flow/graph/list', {
        searchParams: [
          ['page_num', 0],
          ['ordering', 'name'],
          ['category', 'schedule'],
          ['states', 'modified'],
          ['states', 'published']
        ]
      })
      .json<Resp<Page<FlowGraph>>>()
      .then((resp) => {
        graphOptions = resp.data.items.map((g) => ({ value: g.id, label: g.name }));
      });
  });
</script>

<Modal icon={icons.clock} title={$_(id ? 'action.edit' : 'action.add', $_('entity.job'))} bind:this={modal}>
  <form
    method="post"
    use:enhance={({ cancel }) => {
      cancel();
      upsert();
    }}
  >
    <fieldset class="fieldset">
      <Label required>{$_('field.graph')}</Label>
      <Select options={graphOptions} class="w-full" name="graph_id" bind:value={graph_id} disabled={!!id} />
      <Label>{$_('field.bootparams')}</Label>
      <CodeMirror
        darkMode
        minWidth="100%"
        maxWidth="100%"
        maxHeight="10rem"
        language={json()}
        title={$_('field.bootparams')}
        bind:document={bootparams}
      />
      <Label required>{$_('field.trigger')}</Label>
      <Select options={enumToOptions(JobTrigger, false)} class="w-full" name="trigger" bind:value={trigger} />
      {#if trigger === 'date'}
        <Label required>{$_('field.run_date')}</Label>
        <input class="input w-full" bind:value={run_date} {...schema.run_date} />
      {:else if trigger === 'cron'}
        <Label required>{$_('field.cron_expr')}</Label>
        <input placeholder="0 * * * *" class="input w-full" bind:value={cron_expr} {...schema.cron_expr} />
      {:else if trigger === 'interval'}
        <Label required>{$_('field.interval')}</Label>
        <div class="flex gap-2">
          <input class="input w-full" bind:value={interval_num} {...schema.interval_num} />
          <Select
            required
            options={enumToOptions(IntervalUnit, false)}
            class="w-full"
            name="interval_unit"
            bind:value={interval_unit}
          />
        </div>
        <Label>{$_('field.interval_start')}</Label>
        <input class="input w-full" bind:value={interval_start} {...schema.interval_start} />
        <Label>{$_('field.interval_end')}</Label>
        <input class="input w-full" bind:value={interval_end} {...schema.interval_end} />
      {/if}
    </fieldset>
    <div class="modal-action">
      <button type="button" class="btn" onclick={() => modal.close()}>
        {$_('message.cancel')}
      </button>
      <button type="submit" class="btn btn-submit" disabled={$loading !== null || !graph_id}>
        {$_('message.confirm')}
        {#if $loading}
          <span class="loading loading-xs loading-dots"></span>
        {/if}
      </button>
    </div>
  </form>
</Modal>
