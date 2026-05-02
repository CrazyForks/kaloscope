<script lang="ts" module>
  import { isWhite } from '$lib/utils';
  import { v4 as uuidv4 } from 'uuid';

  import type { Danmaku, Definition } from '$lib/types';
  import type { IconifyIcon } from 'iconify-icon';
  import type Player from 'xgplayer';
  import type { IUrl } from 'xgplayer/es/defaultConfig';
  import type DanmakuPlugin from 'xgplayer/es/plugins/danmu';
  import type PlaybackRatePlugin from 'xgplayer/es/plugins/playbackRate';
  import type RotatePlugin from 'xgplayer/es/plugins/rotate';
  import type StartPlugin from 'xgplayer/es/plugins/start';

  // video settings
  type LandscapeMode = 'rotate' | 'web_api';
  type VideoSettings = {
    landscapeMode: LandscapeMode;
  };
  const video = persisted<VideoSettings>('video', {
    landscapeMode: 'rotate'
  });

  // danmaku settings
  type DanmakuMode = 'scroll' | 'top' | 'bottom' | 'color';
  type DanmakuSettings = {
    enabled: boolean;
    blocks: DanmakuMode[];
    area: number;
    opacity: number;
    fontSize: number;
    speed: number;
  };
  const danmaku = persisted<DanmakuSettings>('danmaku', {
    enabled: true,
    blocks: [],
    area: 75,
    opacity: 50,
    fontSize: 20,
    speed: 50
  });

  /**
   * Formats the danmakus to the format required by the player.
   *
   * @param danmakus - The list of video comments.
   * @returns The formatted danmakus.
   */
  export function formatDanmakus(danmakus: Danmaku[] | null | undefined) {
    if (!danmakus || danmakus.length === 0) {
      return [];
    }
    // https://github.com/bytedance/danmu.js
    return danmakus
      .filter((danmaku) => danmaku.text)
      .map(({ id, text, start, duration, mode, color }) => {
        return {
          id: id || uuidv4(), // unique id
          txt: text, // comment text
          start: start || 0, // start time in milliseconds
          duration: duration || undefined, // duration in milliseconds
          moveV: duration ? undefined : 200, // speed of scrolling (pixels per second)
          mode: mode || 'scroll', // display mode: 'scroll', 'top', 'bottom'
          color: !!color && !isWhite(color), // mark the danmaku as colored
          style: {
            color: color || '#fff' // comment color
          }
        };
      });
  }
</script>

<script lang="ts">
  import { Modal, Range, Select } from '$lib/components';
  import { MEDIA_STREAM_PREFIX } from '$lib/constants';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { persisted } from '$lib/stores';
  import { fade } from 'svelte/transition';
  import { Events } from 'xgplayer';

  let { player }: { player: Player | null } = $props();
  // whether the current video is a local media file
  let localMedia: boolean = $derived.by(() => {
    const url = player?.config.url;
    return typeof url === 'string' && url.startsWith(MEDIA_STREAM_PREFIX);
  });

  // the plugins used in the settings
  let startPlugin: StartPlugin | null = $derived.by(() => player?.getPlugin('start'));
  let rotatePlugin: RotatePlugin | null = $derived.by(() => player?.getPlugin('rotate'));
  let danmakuPlugin: DanmakuPlugin | null = $derived.by(() => player?.getPlugin('danmu'));
  let playbackRatePlugin: PlaybackRatePlugin | null = $derived.by(() => player?.getPlugin('playbackRate'));

  // the settings states
  let playbackRates: Record<string, number> = $state({});
  let playbackRate: number = $state(1);
  let rotateDegrees: number[] = $state([0, 90, 180, 270]);
  let rotateDegree: number = $state(0);
  let definitions: Definition[] = $state([]);
  let definition: string = $state('');

  // the modal dialog instance
  let modal: Modal;
  // the current active tab in the settings modal
  let tabId: string = $state('video');
  // whether the player is in rotate fullscreen mode
  let rotateFullscreen: boolean = $state(false);

  /**
   * Show the settings modal.
   */
  export const showModal = () => modal.show();

  /**
   * Get the current landscape mode setting.
   */
  export const landscapeMode = () => $video?.landscapeMode;

  /**
   * Toggle the rotation direction of the modal.
   */
  export function toggleDirection() {
    if (player) {
      const portrait = screen.orientation.type.includes('portrait');
      rotateFullscreen = portrait && player?.isRotateFullscreen;
    }
  }

  /**
   * Initialize the settings.
   */
  export function init() {
    // playback rate
    if (playbackRatePlugin !== null) {
      for (const rate of [...playbackRatePlugin.config.list].reverse()) {
        playbackRates[rate.text] = rate.rate;
      }
      playbackRate = playbackRatePlugin.curValue;
      playbackRatePlugin.on(Events.RATE_CHANGE, () => {
        if (player?.playbackRate && playbackRate !== player.playbackRate) {
          playbackRate = player.playbackRate;
        }
      });
    }
    // definition
    if (player?.config.definitions) {
      definitions = player?.config.definitions;
      if (definitions.length > 0 && typeof player.config.url === 'string') {
        definition = player.config.url;
      }
    }
    // danmaku
    if ($danmaku !== null && danmakuPlugin !== null) {
      danmakuPlugin.setArea({ start: 0, end: $danmaku.area / 100 || 0.1 });
      danmakuPlugin.setOpacity($danmaku.opacity / 100);
      danmakuPlugin.setFontSize($danmaku.fontSize, null);
      danmakuPlugin.danmujs?.setPlayRate('scroll', 1 + ($danmaku.speed - 50) / 100);
      for (const mode of $danmaku.blocks) {
        danmakuPlugin.hideMode(mode);
      }
      if (!$danmaku.enabled) {
        danmakuPlugin.stop();
      }
    }
    loadLocalDanmakus();
  }

  /**
   * Change the video definition.
   *
   * @param url - The new video URL to switch to.
   */
  export function changeDefinition(url: IUrl) {
    if (player) {
      player.setConfig({ url: url });
      const seamless = typeof MediaSource !== 'undefined' && typeof MediaSource.isTypeSupported === 'function';
      if (seamless) {
        // use seamless switching if supported
        player.switchURL(url, { seamless });
      } else {
        const isPlaying = player.isPlaying;
        startPlugin && (startPlugin.config.disableAnimate = true);
        player.switchURL(url)?.then(() => {
          startPlugin && (startPlugin.config.disableAnimate = false);
          // start the player if it was not playing before
          !isPlaying && player?.play();
        });
      }
      if (typeof url === 'string') {
        if (definition !== url) {
          definition = url;
        } else {
          // definition is changed by the select component
          player.emit('url_change', url);
        }
      }
    }
  }

  /**
   * Change the playback rate of the video.
   *
   * @param rate - The new playback rate.
   */
  function changePlaybackRate(rate: number) {
    if (player) {
      player.playbackRate = rate;
      playbackRate = rate;
    }
  }

  /**
   * Change the rotation degree of the video.
   *
   * @param degree - The new rotation degree.
   */
  function changeRotateDegree(degree: number) {
    if (rotatePlugin) {
      rotatePlugin.rotate(true, true, (degree - rotateDegree) / 90);
      rotateDegree = degree;
    }
  }

  /**
   * Load the danmaku data for the current video.
   */
  export function loadLocalDanmakus() {
    if (!localMedia || !danmakuPlugin) {
      return;
    }
    // TODO: load the danmaku data
    const danmakus: Danmaku[] = [];
    danmakuPlugin.clear();
    danmakuPlugin.updateComments(formatDanmakus(danmakus), true);
  }
</script>

<Modal
  bind:this={modal}
  class="video-settings {rotateFullscreen ? 'inset-0 left-full h-dvw w-dvh origin-top-left rotate-90' : ''}"
  boxClass="bg-[hsla(0,0%,10%,0.9)] backdrop-blur-sm {rotateFullscreen ? '' : 'auto-margin-bottom'}"
  cornerClass="text-white/80 [&>button]:hover:bg-white/80"
>
  <div class="tabs-border tabs">
    <!-- The video settings tab. -->
    {@render tabLabel('video', icons.videoFill, $_('media.video.settings'))}
    <div class="tab-content">
      <div>
        {@render optionName($_('media.video.speed'))}
        <span
          class="join grid rounded-field"
          style="grid-template-columns: repeat({Object.keys(playbackRates).length}, minmax(0, 1fr));"
        >
          {#each Object.entries(playbackRates) as [text, rate] (rate)}
            <button
              class="btn join-item {playbackRate === rate ? 'btn-active' : ''}"
              onclick={() => changePlaybackRate(rate)}
            >
              {text}
            </button>
          {/each}
        </span>
      </div>
      <div>
        {@render optionName($_('media.video.rotate'))}
        <span
          class="join grid rounded-field"
          style="grid-template-columns: repeat({rotateDegrees.length}, minmax(0, 1fr));"
        >
          {#each rotateDegrees as degree (degree)}
            <button
              class="btn join-item {rotateDegree === degree ? 'btn-active' : ''}"
              onclick={() => changeRotateDegree(degree)}
            >
              {degree}°
            </button>
          {/each}
        </span>
      </div>
      <div>
        {@render optionName($_('media.video.definition'))}
        {#if definitions.length > 0}
          <Select
            native={!rotateFullscreen}
            options={definitions.map((d) => ({ value: d.url, label: String(d.definition) }))}
            bind:value={definition}
            onchange={() => changeDefinition(definition)}
            class="dropdown-top [&_p]:max-h-32!"
          />
        {:else}
          <Select options={[{ value: '', label: 'media.video.default' }]} disabled />
        {/if}
      </div>
      {#if $video !== null}
        <div>
          {@render optionName($_('media.video.landscape.title'), true)}
          <Select
            native={!rotateFullscreen}
            options={[
              { value: 'rotate', label: 'media.video.landscape.rotate' },
              { value: 'web_api', label: 'media.video.landscape.web_api' }
            ]}
            bind:value={$video.landscapeMode}
            class="dropdown-top [&_p]:max-h-32!"
          />
        </div>
      {/if}
    </div>

    <!-- The danmaku settings tab. -->
    {@render tabLabel('danmaku', icons.danmakuFill, $_('media.danmaku.settings'))}
    <div class="tab-content">
      {#if $danmaku !== null && danmakuPlugin !== null}
        <div>
          {@render optionName($_('media.danmaku.toggle'), true)}
          <input
            type="checkbox"
            class="toggle"
            bind:checked={$danmaku.enabled}
            onchange={() => {
              $danmaku.enabled ? danmakuPlugin.start() : danmakuPlugin.stop();
            }}
          />
        </div>
        <div>
          {@render optionName($_('media.danmaku.block'), true)}
          <div class="flex-center gap-6">
            {@render danmakuBlock('scroll', icons.mist)}
            {@render danmakuBlock('top', icons.alignBoxCenterTop)}
            {@render danmakuBlock('bottom', icons.alignBoxCenterBottom)}
            {@render danmakuBlock('color', icons.palette)}
          </div>
        </div>
        <div>
          {@render optionName($_('media.danmaku.area'), true)}
          <Range
            bind:value={$danmaku.area}
            values={[10, 25, 50, 75, 100]}
            class="pt-2"
            textClass="text-white/80"
            sliderClass="range-primary"
            onchange={(value) => {
              danmakuPlugin.setArea({ start: 0, end: value / 100 });
            }}
          />
        </div>
        <div>
          {@render optionName($_('media.danmaku.opacity'), true)}
          <Range
            bind:value={$danmaku.opacity}
            class="pt-2"
            textClass="text-white/80"
            sliderClass="range-primary"
            onchange={(value) => {
              danmakuPlugin.setOpacity(value / 100);
            }}
          />
        </div>
        <div>
          {@render optionName($_('media.danmaku.font_size'), true)}
          <Range
            bind:value={$danmaku.fontSize}
            min={5}
            max={50}
            unit="px"
            class="pt-2"
            textClass="text-white/80"
            sliderClass="range-primary"
            onchange={(value) => {
              danmakuPlugin.setFontSize(value, null);
            }}
          />
        </div>
        <div>
          {@render optionName($_('media.danmaku.speed'), true)}
          <Range
            bind:value={$danmaku.speed}
            values={[0.5, 0.75, '1.0', 1.25, 1.5]}
            unit="x"
            class="pt-2"
            textClass="text-white/80"
            sliderClass="range-primary"
            onchange={(value) => {
              danmakuPlugin.danmujs?.setPlayRate('scroll', value);
            }}
          />
        </div>
      {/if}
    </div>

    <!-- The danmaku match tab. -->
    {#if localMedia}
      {@render tabLabel('match', icons.boxMultipleSearchFilled, $_('media.danmaku.match'))}
      <div class="tab-content"></div>
    {/if}
  </div>
</Modal>

<!-- The tab label rendering snippet. -->
{#snippet tabLabel(id: string, icon: IconifyIcon, name: string)}
  {@const checked = tabId === id}
  {@const tabClass = checked ? '!text-white/80' : '!text-white/20 hover:!text-white/80'}
  <label class="tab mb-4 h-8 gap-1 rounded-field px-2 transition-colors {tabClass}">
    <input type="radio" {checked} value={id} bind:group={tabId} />
    <iconify-icon {icon} width="1.125rem" class="mt-0.5 size-4.5"></iconify-icon>
    <span class="text-lg font-bold">{name}</span>
  </label>
{/snippet}

<!-- The option name rendering snippet. -->
{#snippet optionName(name: string, global: boolean = false)}
  <span class="max-w-40 min-w-20 shrink-0 font-semibold text-white/60 {global ? '' : 'italic'}">{name}</span>
{/snippet}

<!-- The danmaku block rendering snippet. -->
{#snippet danmakuBlock(mode: DanmakuMode, icon: IconifyIcon)}
  {@const blocked = $danmaku?.blocks.includes(mode)}
  <button
    class="flex-col-center cursor-pointer transition-all {blocked ? 'text-white/80' : 'text-white/20'}"
    onclick={() => {
      if ($danmaku !== null && danmakuPlugin !== null) {
        if (blocked) {
          $danmaku.blocks = $danmaku.blocks.filter((m) => m !== mode);
          danmakuPlugin.showMode(mode);
        } else {
          $danmaku.blocks = [...$danmaku.blocks, mode];
          danmakuPlugin.hideMode(mode);
        }
      }
    }}
  >
    <span class="relative flex-center size-8">
      <iconify-icon {icon} width="1.75rem"></iconify-icon>
      {#if blocked}
        <iconify-icon
          icon={icons.line}
          width="2rem"
          class="absolute text-gray-200"
          rotate="90deg"
          transition:fade={{ duration: 150 }}
        ></iconify-icon>
      {/if}
    </span>
    <span class="text-xs">{$_(`media.danmaku.mode.${mode}`)}</span>
  </button>
{/snippet}

<style>
  :global {
    .video-settings {
      .auto-margin-bottom {
        @media (height >= 40rem) {
          margin-bottom: var(--ks-dock-h);
        }
      }

      .select {
        --size: 1.5rem;
        font-size: 0.6875rem;
        width: 8rem;
        color: color-mix(in oklab, #fff 80%, transparent);
        border-color: color-mix(in oklab, #fff 10%, transparent);
        background-color: hsla(0, 0%, 20%, 0.9);
        box-shadow: unset;
        &:is(:disabled, [disabled]) {
          opacity: 0.3;
        }
      }
    }
  }

  .tab-content {
    max-height: 30rem;
    border-radius: 0;
    border-top-color: color-mix(in oklab, #fff 10%, transparent);
    > div {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      min-height: 3.25rem;
      margin-top: 0.25rem;
      &:first-child {
        margin-top: 0.75rem;
      }
    }
  }

  .toggle {
    background-color: color-mix(in oklab, #fff 60%, transparent);
    box-shadow: unset;
    &:checked {
      color: var(--color-primary-content);
      border-color: var(--color-primary);
      background-color: var(--color-primary);
    }
  }

  .btn {
    --size: 1.5rem;
    --btn-p: 0.5rem;
    --fontsize: 0.6875rem;
    color: color-mix(in oklab, #fff 20%, transparent);
    border-color: hsla(0, 0%, 20%, 0.9);
    background-color: hsla(0, 0%, 20%, 0.9);
    box-shadow: unset;
    &.btn-active {
      color: color-mix(in oklab, #fff 80%, transparent);
      border-color: hsla(0, 0%, 30%, 0.9);
      background-color: hsla(0, 0%, 30%, 0.9);
    }
  }
</style>
