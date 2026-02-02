<script lang="ts" module>
  import type { Nav } from '$lib/types';

  export type DockProps = {
    /** List of navigation items. */
    navs: Nav[];
    /** Whether to show the shadow. */
    shadow?: boolean;
  };
</script>

<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { _ } from '$lib/i18n';
  import { subroutes } from '$lib/stores';
  import { scale } from 'svelte/transition';

  let { navs, shadow = true }: DockProps = $props();
  // box shadow class
  let shadowClass = $derived(shadow ? 'shadow-[0_-1px_2px_0_rgba(0,0,0,0.05)]' : '');
</script>

<div class="dock layer-2 h-(--ks-dock-h) border-t bg-blur-90 py-0 sm:hidden {shadowClass}">
  {#each navs.filter((nav) => nav.mobile) as nav (nav.path)}
    {@const active = page.url.pathname.startsWith(nav.path)}
    <a
      href={nav.path}
      class="mt-4 mb-0 justify-start duration-0 {active ? 'pointer-events-none text-content' : ''}"
      onclick={(event) => {
        if (active) {
          event.preventDefault();
          return;
        }
        // navigate to subroute if exists
        const subroute = $subroutes?.[nav.path];
        if (subroute) {
          event.preventDefault();
          goto(subroute, { replaceState: true });
        }
      }}
    >
      <div class="size-5">
        {#if active}
          <iconify-icon icon={nav.iconFilled} width="1.25rem" in:scale={{ duration: 200 }}></iconify-icon>
        {:else}
          <iconify-icon icon={nav.icon} width="1.25rem" class="opacity-70"></iconify-icon>
        {/if}
      </div>
      <span class="dock-label font-title {active ? '' : 'opacity-70'}">
        {$_(nav.title)}
      </span>
    </a>
  {/each}
</div>
