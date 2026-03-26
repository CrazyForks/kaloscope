<script lang="ts" module>
  import type { FlowTrigger, MediaLib, Resp } from '$lib/types';
  import { enumToOptions, LibType } from '$lib/enums';

  type MediaLibEditorProps = Partial<{
    id: number;
    lib_type: keyof typeof LibType;
    name: string;
    dir: string;
    language: string | null;
    triggers: FlowTrigger[];
    onsave: (result: MediaLib) => void;
  }>;
</script>

<script lang="ts">
  import { enhance } from '$app/forms';
  import { api } from '$lib/api';
  import { FileTree, FlowTriggers, Label, Modal, Select } from '$lib/components';
  import { createFormSchema, createLoading } from '$lib/helpers';
  import { _, locales } from '$lib/i18n';
  import { icons } from '$lib/icons';

  let { id, lib_type, name, dir, language = '', triggers, onsave }: MediaLibEditorProps = $props();

  // the file tree instance
  let fileTree: FileTree;

  // the modal dialog instance
  let modal: Modal;
  export const showModal = () => modal.show();

  // the loading state and form schema
  const loading = createLoading();
  const schema = createFormSchema(({ text }) => ({
    name: text().maxlength(64),
    dir: text().maxlength(4096)
  }));

  /**
   * Save or update the media library.
   *
   * @param form - The form element.
   * @param data - The form data.
   */
  function upsert(form: HTMLFormElement, data: FormData) {
    loading.start();
    const jsonData: Record<string, unknown> = Object.fromEntries(data);
    jsonData.id = id;
    jsonData.triggers = triggers;
    api
      .post('media/lib/upsert', { json: jsonData })
      .json<Resp<MediaLib>>()
      .then((resp) => {
        modal.close();
        onsave?.(resp.data);
        setTimeout(() => form.reset(), 200);
      })
      .finally(() => {
        loading.end();
      });
  }
</script>

<Modal
  icon={icons.videoClipMultiple}
  title={$_(id ? 'action.edit' : 'action.add', $_('entity.media_lib'))}
  bind:this={modal}
>
  <form
    method="post"
    use:enhance={({ formElement, formData, cancel }) => {
      cancel();
      upsert(formElement, formData);
    }}
  >
    <fieldset class="fieldset">
      <Label required>{$_('field.type')}</Label>
      <Select
        options={enumToOptions(LibType, false)}
        bind:value={lib_type}
        name="lib_type"
        class="w-full"
        disabled={!!id}
      />
      <Label required>{$_('field.name')}</Label>
      <input placeholder={$_('field.name')} class="input w-full" bind:value={name} {...schema.name} />
      <Label required>{$_('field.dir')}</Label>
      <!-- svelte-ignore a11y_consider_explicit_label -->
      <button
        type="button"
        class="input w-full {id ? 'cursor-not-allowed' : 'cursor-pointer'}"
        onclick={() => fileTree.showModal()}
        disabled={!!id}
      >
        <iconify-icon icon={icons.folder} width="1.5rem" class="opacity-50"></iconify-icon>
        <input
          placeholder={$_('action.select', $_('field.dir'))}
          autocomplete="off"
          class="grow {id ? 'cursor-not-allowed' : 'cursor-pointer'}"
          bind:value={dir}
          {...schema.dir}
          disabled={!!id}
        />
      </button>
      <Label>{$_('field.language')}</Label>
      <Select bind:value={language} name="language" class="w-full">
        <option value="">{$_('enum.none')}</option>
        {#each $locales.filter((l) => l !== 'languages') as code (code)}
          <option value={code}>{$_(code, { locale: 'languages' })}</option>
        {/each}
      </Select>
      <FlowTriggers class="mt-4" category="ingest" {triggers} onchange={(newTriggers) => (triggers = newTriggers)} />
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

<FileTree bind:this={fileTree} onlyDirs={true} onconfirm={(stats) => (dir = stats.path)} />
