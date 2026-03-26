<script lang="ts" module>
  import type { Filter } from '$lib/types';

  export type FiltersProps = {
    /** The search filters schema. */
    schema: Record<string, Filter>;
    /** The search filters JSON value. */
    value?: string | null;
    /** The callback function when the dialog is closed. */
    onclose?: (value: string) => void;
  };
</script>

<script lang="ts">
  import { Label, Modal } from '$lib/components';
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';

  let { schema, value, onclose }: FiltersProps = $props();

  // svelte-ignore state_referenced_locally
  let values: Record<string, string | string[]> = $state(JSON.parse(value || '{}'));

  let modal: Modal;
  export const showModal = () => modal.show();

  /**
   * Check if the element is covered by the bottom of the screen.
   *
   * @param element - The element to check.
   * @returns True if the element is covered, false otherwise.
   */
  function isBottomOverflow(element: Element | null): boolean {
    if (!element) {
      return false;
    }
    return element.getBoundingClientRect().bottom > window.innerHeight;
  }
</script>

<Modal
  icon={icons.adjustmentsHorizontal}
  title={$_('entity.filters')}
  bind:this={modal}
  onclose={() => onclose?.(JSON.stringify(values))}
>
  <fieldset class="mb-2 fieldset">
    {#each Object.entries(schema) as [key, filter] (key)}
      <Label>{filter.label ?? key}</Label>
      {#if filter.type === 'text'}
        {@render text(key, filter)}
      {:else if filter.type === 'radio'}
        {@render radio(key, filter)}
      {:else if filter.type === 'checkbox'}
        {@render checkbox(key, filter)}
      {:else if filter.type === 'calendar'}
        {@render calendar(key, filter)}
      {/if}
    {/each}
  </fieldset>
</Modal>

<!-- text filter -->
<!-- eslint-disable-next-line @typescript-eslint/no-unused-vars -->
{#snippet text(key: string, filter: Filter)}
  <input
    type="search"
    class="input w-full"
    value={values[key]}
    oninput={(event) => {
      values[key] = event.currentTarget.value;
      if (values[key] === '') {
        delete values[key];
      }
    }}
  />
{/snippet}

<!-- radio filter -->
{#snippet radio(key: string, filter: Filter)}
  {#if filter.options}
    <div class="flex flex-wrap gap-4">
      {#each Object.entries(filter.options) as [name, value] (name)}
        <label class="label">
          <input
            type="radio"
            class="radio"
            {name}
            checked={values[key] === name}
            onclick={() => {
              if (values[key] === name) {
                delete values[key];
              } else {
                values[key] = name;
              }
            }}
          />
          {value}
        </label>
      {/each}
    </div>
  {/if}
{/snippet}

<!-- checkbox filter -->
{#snippet checkbox(key: string, filter: Filter)}
  {#if filter.options}
    <div class="flex flex-wrap gap-4">
      {#each Object.entries(filter.options) as [name, value] (name)}
        <label class="label">
          <input
            type="checkbox"
            class="checkbox"
            {name}
            checked={values[key]?.includes(name)}
            onclick={() => {
              const names = (values[key] ?? []) as string[];
              if (names.includes(name)) {
                values[key] = names.filter((n) => n !== name);
                if (values[key].length === 0) {
                  delete values[key];
                }
              } else {
                values[key] = [...names, name];
              }
            }}
          />
          {value}
        </label>
      {/each}
    </div>
  {/if}
{/snippet}

<!-- calendar filter -->
<!-- eslint-disable-next-line @typescript-eslint/no-unused-vars -->
{#snippet calendar(key: string, filter: Filter)}
  {@const callyId = `cally-${key}`}
  {@const callyPopoverId = `cally-popover-${key}`}
  <button
    id={callyId}
    popovertarget={callyPopoverId}
    type="button"
    class="input w-full"
    style="anchor-name:--{callyId}"
    onclick={() => {
      setTimeout(() => {
        const element = document.querySelector(`#${callyPopoverId}`);
        if (isBottomOverflow(element)) {
          element?.classList.add('dropdown-top');
        }
        element?.classList.remove('invisible');
      }, 0);
    }}
  >
    <iconify-icon icon={icons.calendar} width="1.25rem" class="opacity-70"></iconify-icon>
    {values[key]}
    {#if values[key]}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <div
        tabindex="0"
        role="button"
        class="absolute right-2 flex-center cursor-pointer"
        onclick={(event) => {
          event.preventDefault();
          delete values[key];
        }}
      >
        <iconify-icon
          icon={icons.dismissCircleFilled}
          width="1.25rem"
          class="text-base-content/50 transition-colors duration-200 hover:text-base-content"
        ></iconify-icon>
      </div>
    {/if}
  </button>
  <div
    popover
    id={callyPopoverId}
    class="dropdown invisible m-1 rounded-box border bg-base-100 shadow-xl transition-none"
    style="position-anchor:--{callyId}"
  >
    <calendar-date
      class="cally [&_::part(day_selected):hover]:bg-base-content/80 [&_::part(day_today):hover]:bg-primary/80"
      value={values[key]}
      onfocusday={(event: { target: { value: string } }) => {
        const newValue = event.target.value;
        if (values[key] !== newValue) {
          values[key] = newValue;
          document.getElementById(callyPopoverId)?.hidePopover();
        }
      }}
    >
      {/* @ts-expect-error property does not exist */ null}
      <svg
        aria-label="Previous"
        class="size-4 fill-current"
        slot="previous"
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"><path d="M15.75 19.5 8.25 12l7.5-7.5"></path></svg
      >
      {/* @ts-expect-error property does not exist */ null}
      <svg
        aria-label="Next"
        class="size-4 fill-current"
        slot="next"
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"><path d="m8.25 4.5 7.5 7.5-7.5 7.5"></path></svg
      >
      <calendar-month></calendar-month>
    </calendar-date>
  </div>
{/snippet}
