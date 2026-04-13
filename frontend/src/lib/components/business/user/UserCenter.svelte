<script lang="ts" module>
  import type { DropdownProps } from '$lib/components';
  import type { Nav } from '$lib/types';
  import type { IconifyIcon } from 'iconify-icon';
  import type { MouseEventHandler } from 'svelte/elements';
  import { MediaQuery } from 'svelte/reactivity';

  type UserCenterProps = {
    /** Navigation items. */
    navs?: Nav[];
    /** Function to switch the theme. */
    switchTheme?: () => void;
    /** Function to switch the language. */
    switchLanguage?: () => void;
  } & Pick<DropdownProps, 'class' | 'triggerClass' | 'contentClass'>;

  // the standalone display mode media query
  const standaloneMode = new MediaQuery('(display-mode: standalone)');

  // the deferred prompt event for PWA installation
  // https://developer.mozilla.org/en-US/docs/Web/API/BeforeInstallPromptEvent
  type BeforeInstallPromptEvent = Event & { prompt: () => void };
  let deferredPrompt: BeforeInstallPromptEvent | null = $state(null);

  // whether the app is in fullscreen mode
  let isFullscreen: boolean = $state(fullscreen.isFullscreen());
</script>

<script lang="ts">
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import { Dropdown, Image, confirm } from '$lib/components';
  import { _, duration } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import { token, user } from '$lib/stores';
  import { fullscreen } from '$lib/utils';

  let { navs = [], switchTheme, switchLanguage, class: _class, triggerClass, contentClass }: UserCenterProps = $props();

  // the duration of the user's login time
  let loginDuration: string = $state($duration($user?.login_at));

  /**
   * Logout the user.
   */
  function logout() {
    api.post('auth/logout').then(() => {
      token.set(null);
      goto('/login');
    });
  }
</script>

<svelte:window
  onbeforeinstallprompt={(event: Event) => {
    event.preventDefault();
    deferredPrompt = event as BeforeInstallPromptEvent;
  }}
/>

<svelte:document onfullscreenchange={() => (isFullscreen = fullscreen.isFullscreen())} />

{#snippet option(icon: string | IconifyIcon, text: string, onclick: MouseEventHandler<HTMLElement>, _class?: string)}
  <li class={_class}>
    <button
      onclick={(event) => {
        onclick?.(event);
        event.currentTarget.blur();
      }}
    >
      <iconify-icon {icon} width="1.25rem" class="size-5"></iconify-icon>
      {text}
    </button>
  </li>
{/snippet}

<Dropdown class={_class} {triggerClass} {contentClass} onclick={() => (loginDuration = $duration($user?.login_at))}>
  {#snippet trigger()}
    <Image
      circle
      shadow
      sluggish
      src={$user?.avatar}
      text={$user?.username}
      icon={icons.user}
      class="pointer-events-none mx-2"
    />
  {/snippet}

  <ul class="menu gap-1">
    {#if $user}
      <div class="text-center text-lg font-bold text-surface">
        {$user.username}
      </div>
    {/if}
    {#if loginDuration}
      <div class="text-center text-xs opacity-60">
        {`${$_('session.duration')}: ${loginDuration}`}
      </div>
    {/if}

    <div class="divider my-0"></div>
    {@render option(icons.language, $_('app.switch_language'), () => switchLanguage && switchLanguage(), 'sm:hidden')}
    {@render option(icons.colorSwatch, $_('app.switch_theme'), () => switchTheme && switchTheme(), 'sm:hidden')}
    {#if isFullscreen}
      {@render option(icons.fullScreenMinimizeFilled, $_('app.exit_fullscreen'), () => fullscreen.exit())}
    {:else}
      {@render option(icons.fullScreenMaximizeFilled, $_('app.enter_fullscreen'), () => fullscreen.enter())}
    {/if}

    <div class="divider my-0"></div>
    {#each navs.filter((nav) => !nav.mobile) as nav (nav.path)}
      {@render option(nav.icon, $_(nav.title), () => goto(nav.path), 'sm:hidden')}
    {/each}
    {@render option(icons.lockClosedKey, $_('nav.settings.personal.account'), () => goto('/settings/personal/account'))}
    {@render option(icons.alertUrgent, $_('app.notifications'), () => {})}

    <div class="divider my-0"></div>
    {#if !standaloneMode.current && deferredPrompt}
      {@render option(icons.desktopArrowDown, $_('app.pwa_install'), () => deferredPrompt?.prompt())}
    {/if}
    {@render option(icons.stars, $_('app.check_updates'), () => {
      window.open('https://hub.docker.com/r/kaloscope/kaloscope/tags', '_blank', 'noreferrer');
    })}

    <div class="divider my-0"></div>
    {@render option(
      icons.signOut,
      $_('app.logout'),
      () => confirm({ icon: icons.signOut, title: $_('app.logout'), onconfirm: logout }),
      'text-base-content/60 font-semibold'
    )}
  </ul>
</Dropdown>
