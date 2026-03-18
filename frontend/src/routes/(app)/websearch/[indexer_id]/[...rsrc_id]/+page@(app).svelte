<script lang="ts">
  import { page } from '$app/state';
  import { api } from '$lib/api';
  import { Overlay, VideoPlayer } from '$lib/components';
  import { createLoading } from '$lib/helpers';
  import type { Chapter, Resource, Resp } from '$lib/types';
  import { onMount, tick } from 'svelte';
  import { queryParameters, ssp } from 'sveltekit-search-params';

  // the URL query parameters
  const query = queryParameters(
    {
      chapter_id: ssp.string(),
      media_type: ssp.string(),
      video_type: ssp.string()
    },
    {
      pushHistory: false
    }
  );

  // the websearch result
  let resource: Resource | null = $state(null);
  let mediaType: string | null = $derived.by(() => resource?.media_type ?? query.media_type);
  let videoType: string | null = $derived.by(() => resource?.video_type ?? query.video_type);

  // the video player instance
  let player: VideoPlayer | null = $state(null);
  let refreshKey: number = $state(0);

  // the loading state
  const loading = createLoading();

  /**
   * Fetch the details of the resource.
   *
   * @param refresh - Whether to refresh the player.
   */
  function details(refresh: boolean = false) {
    loading.start();
    api
      .post(`flow/graph/${page.params.indexer_id}/execute`, {
        json: {
          $start: 'details_start',
          id: page.params.rsrc_id,
          chapter_id: query.chapter_id
        }
      })
      .json<Resp<Resource>>()
      .then((resp) => {
        refresh && (refreshKey = new Date().getTime());
        tick().then(() => onload(resp.data));
      })
      .finally(() => {
        loading.end();
      });
  }

  /**
   * Handle the resource load event.
   *
   * @param rsrc - The resource object.
   */
  function onload(rsrc: Resource) {
    resource = rsrc;
    const chapters = rsrc?.chapters ?? [];
    if (rsrc?.url) {
      if (mediaType === 'video' && player) {
        player.mount({
          url: rsrc.url,
          videoType: videoType,
          danmakus: rsrc.danmakus,
          chapters: chapters,
          chapterId: query.chapter_id,
          chapterChange: onchange,
          definitions: rsrc.definitions,
          title: rsrc.title,
          uploader: rsrc.uploader,
          uploadedAt: rsrc.uploaded_at
        });
      }
    } else if (chapters.length > 0) {
      onchange(chapters[0]);
    }
  }

  /**
   * Handle the chapter change event.
   *
   * @param chapter - The chapter object.
   */
  function onchange(chapter: Chapter) {
    const { id, url, title, definition } = chapter;
    if (url) {
      // switch the resource URL
      if (mediaType === 'video' && player) {
        player.mount({ next: !definition, url: url, title: title });
      }
    } else if (id && id !== query.chapter_id) {
      // update the query parameter and request the new chapter
      query.chapter_id = id;
      details(true);
    }
  }

  onMount(() => {
    details();
  });
</script>

<div class="history-back fixed inset-0 layer-1 max-sm:bottom-(--ks-dock-h)">
  <Overlay black loading={$loading} />
  {#key refreshKey}
    <VideoPlayer bind:this={player} />
  {/key}
</div>
