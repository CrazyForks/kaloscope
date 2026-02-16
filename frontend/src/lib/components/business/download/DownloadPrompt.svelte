<script lang="ts" module>
  import { api } from '$lib/api';
  import { createLoading } from '$lib/helpers';
  import type { DownloadDir, Downloader, Resp } from '$lib/types';

  // the modal dialog and file tree instances
  let modal: Modal;
  let fileTree: FileTree;

  // the download directories and managers
  let directories: DownloadDir[] = $state([]);
  let directory: DownloadDir | null = $state(null);
  let downloaders: Downloader[] = $state([]);
  let downloader: number = $state(0);

  // the torrent input and magnet link
  let files: FileList | null = $state(null);
  let link: string = $state('');

  // the submittable state
  let submittable = $derived.by(() => {
    if (files && files.length > 0) {
      return true;
    }
    return link.trim() !== '';
  });

  // the loading state
  const loading = createLoading();

  // the oncreate callback
  let oncreate: (() => void) | null;

  /**
   * Show the download prompt modal.
   *
   * @param uri - The URI to download, if any.
   * @param callback - Optional callback to execute after the download is created.
   */
  export function downloadPrompt(uri?: string | null, callback?: () => void) {
    init().then(() => {
      if (downloader === 0) {
        alert({ level: 'error', message: 'get_downloaders_failed' });
        return;
      }
      oncreate = callback ?? null;
      files = null;
      link = uri || '';
      if (!link && navigator.clipboard) {
        navigator.clipboard
          .readText()
          .then((text) => (link = text))
          .finally(() => modal.show());
      } else {
        modal.show();
      }
    });
  }

  /**
   * Initialize the component.
   */
  async function init() {
    const [_directories, _downloaders] = await Promise.all([getDirectories(), getDownloaders()]);
    directories = _directories;
    directory = directories[0] || null;
    downloaders = _downloaders;
    downloader = downloaders.find((d) => d.status !== 'down')?.id || 0;
  }

  /**
   * Get the download directories.
   */
  async function getDirectories(): Promise<DownloadDir[]> {
    try {
      const resp = await api.get('download/dir/list').json<Resp<DownloadDir[]>>();
      return resp.data;
    } catch (error) {
      console.error(error);
      return [];
    }
  }

  /**
   * Get the downloaders.
   */
  async function getDownloaders(): Promise<Downloader[]> {
    try {
      const resp = await api.get('download/manager/list').json<Resp<Downloader[]>>();
      return resp.data;
    } catch (error) {
      console.error(error);
      return [];
    }
  }

  /**
   * Add a download task.
   *
   * @param form - The form element.
   * @param data - The form data.
   */
  function addTask(form: HTMLFormElement, data: FormData) {
    loading.start();
    // add the pause field
    data.append('pause', (!data.get('start')).toString());
    data.delete('start');
    api
      .post('download/add', { body: data })
      .then(() => {
        modal.close();
        oncreate?.();
        setTimeout(() => form.reset(), 200);
      })
      .finally(() => {
        loading.end();
      });
  }
</script>

<script lang="ts">
  import { enhance } from '$app/forms';
  import { FileTree, Label, Modal, Select, alert } from '$lib/components';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { onMount } from 'svelte';

  let _modal: Modal;
  let _fileTree: FileTree;

  onMount(() => {
    // assign the modal dialog and file tree instances to the module variables
    modal = _modal;
    fileTree = _fileTree;
  });
</script>

<Modal icon={icons.download} title={$_('action.add', $_('model.download'))} bind:this={_modal}>
  <form
    method="post"
    enctype="multipart/form-data"
    use:enhance={({ formElement, formData, cancel }) => {
      cancel();
      addTask(formElement, formData);
    }}
  >
    <fieldset class="fieldset">
      <Label>{$_('download.downloader.title')}</Label>
      <Select
        options={downloaders.map((d) => ({
          value: d.id,
          label: d.name,
          disabled: d.status === 'down'
        }))}
        bind:value={downloader}
        class="w-full"
        name="downloader_id"
      />
      <Label>{$_('download.dir')}</Label>
      {#if directory}
        <button type="button" class="input w-full cursor-pointer" onclick={() => fileTree.showModal()}>
          <iconify-icon icon={icons.folder} width="1.5rem" class="opacity-70"></iconify-icon>
          <input
            type="text"
            autocomplete="off"
            class="grow cursor-pointer truncate text-left direction-rtl"
            value={directory.path.split('').reverse().join('')}
            readonly
          />
          <input type="text" class="hidden" name="dir" value={directory.path} />
          <span class="italic-text">{directory.free}</span>
        </button>
      {:else}
        <label class="input w-full">
          <iconify-icon icon={icons.folder} width="1.5rem" class="opacity-70"></iconify-icon>
          <input type="text" class="grow" name="dir" />
        </label>
      {/if}
      <Label class="mt-6">{$_('download.prompt')}</Label>
      <textarea
        rows={5}
        placeholder={$_('download.supported')}
        class="textarea w-full"
        name="link"
        bind:value={link}
        disabled={files && files.length > 0}
      ></textarea>
      <input type="file" accept=".torrent" class="file-input w-full file-input-xs" name="torrent" bind:files />
      <label class="mt-2 fieldset-label w-fit">
        <input type="checkbox" class="checkbox" checked={true} name="start" />
        <span class="text-base text-base-content opacity-90">{$_('download.start')}</span>
      </label>
    </fieldset>
    <div class="modal-action">
      <button type="button" class="btn" onclick={() => modal.close()}>
        {$_('message.cancel')}
      </button>
      <button type="submit" class="btn btn-submit" disabled={!submittable || $loading !== null}>
        {$_('message.confirm')}
        {#if $loading}
          <span class="loading loading-xs loading-dots"></span>
        {/if}
      </button>
    </div>
  </form>
</Modal>

<FileTree
  bind:this={_fileTree}
  onlyDirs={true}
  onconfirm={(stats) => {
    directory = {
      path: stats.path,
      free: stats.free ?? ''
    };
  }}
/>
