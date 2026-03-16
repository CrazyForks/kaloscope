<script lang="ts">
  import { enhance } from '$app/forms';
  import { api } from '$lib/api';
  import { CodeMirror, Label, Modal, alert } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { json } from '@codemirror/lang-json';

  let { onconfirm }: { onconfirm?: () => void } = $props();
  let graphId = $state(0);
  let bootparams = $state('{}');

  // the modal dialog instance
  let modal: Modal;
  export const showModal = (id: number) => {
    graphId = id;
    modal.show();
  };

  // the loading state
  const loading = createLoading();

  /**
   * Execute the flow graph.
   */
  function execute() {
    // validate the boot parameters
    let params = null;
    try {
      params = JSON.parse(bootparams);
    } catch (error) {
      console.error(error);
    }
    if (typeof params !== 'object' || Array.isArray(params) || params === null) {
      alert({ level: 'error', message: 'invalid_boot_params' });
      return;
    }
    // execute the flow graph
    loading.start();
    api
      .post(`flow/graph/${graphId}/execute`, { json: params })
      .then(() => {
        modal.close();
        onconfirm?.();
      })
      .finally(() => {
        loading.end();
      });
  }
</script>

<Modal icon={icons.playFilled} title={$_('action.execute', $_('entity.graph'))} bind:this={modal}>
  <form
    method="post"
    use:enhance={({ cancel }) => {
      cancel();
      execute();
    }}
  >
    <fieldset class="fieldset">
      <Label required>{$_('flow.exec.bootparams')}</Label>
      <CodeMirror
        darkMode
        minWidth="100%"
        maxWidth="100%"
        maxHeight="18rem"
        language={json()}
        title={$_('flow.exec.bootparams')}
        bind:document={bootparams}
      />
    </fieldset>
    <div class="modal-action">
      <button type="button" class="btn" onclick={() => modal.close()}>
        {$_('message.cancel')}
      </button>
      <button type="submit" class="btn btn-submit" disabled={$loading !== null}>
        {$_('message.confirm')}
        {#if $loading}
          <span class="loading loading-xs loading-dots"></span>
        {/if}
      </button>
    </div>
  </form>
</Modal>
