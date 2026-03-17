<script lang="ts" module>
  export type BackdropProps = {
    /** The source URL of the background image. */
    src: string | null;
    /** Whether to proxy the image through the server. */
    proxy?: boolean | 'store';
    /** The value for the blur filter applied to the background image. */
    blur?: string;
    /** The opacity of the background image. */
    opacity?: string;
  };
</script>

<script lang="ts">
  import { proxyImage } from '$lib/api';
  import { fade } from 'svelte/transition';

  let { src, proxy = false, blur = '5px', opacity = '0.2' }: BackdropProps = $props();
</script>

{#if src}
  {#key src}
    <div
      class="pointer-events-none fixed inset-0 z-0"
      style="
        background-image: url({proxyImage(src, proxy)});
        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
        opacity: {opacity};
        filter: blur({blur});
        mask: radial-gradient(ellipse 80% 70% at center 60%, black 0%, black 50%, transparent 80%);
      "
      transition:fade
    ></div>
  {/key}
{/if}
