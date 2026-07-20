import { icons, iconToSVG } from '$lib/icons';
import type { Chapter } from '$lib/types';
import { isTranscodedStream } from '$lib/utils';
import OptionList from 'xgplayer/es/plugins/common/optionList';
import OptionsIcon from 'xgplayer/es/plugins/common/optionsIcon';
import './index.css';

/**
 * The option attributes used by xgplayer's delegated option list.
 */
type AttrObject = {
  [key: string]: string | number | undefined;
  index?: number;
};

/**
 * The previous and selected chapter option values.
 */
type ChangeData = {
  from: AttrObject | null;
  to: AttrObject;
};

/**
 * A delegated DOM event emitted by xgplayer's option list.
 */
type DelegateEvent = Event & {
  delegateTarget: Element;
};

/**
 * Chapter selector with playback pipeline and progress marker support.
 */
export default class Chapters extends OptionsIcon {
  /**
   * The xgplayer plugin name.
   */
  static get pluginName() {
    return 'chapters';
  }

  /**
   * The default chapter selector configuration.
   */
  static get defaultConfig() {
    return {
      ...OptionsIcon.defaultConfig,
      className: 'xgplayer-chapters',
      isShowIcon: true,
      heightLimit: false,
      hidePortrait: false,
      chapterId: '',
      chapterChange: null
    };
  }

  /**
   * Close other option menus before opening the chapter selector.
   */
  onIconClick = () => {
    for (const name of ['definitions', 'playbackRate']) {
      const plugin = this.player.getPlugin(name);
      if (plugin && plugin.optionsList && plugin.isActive) {
        plugin.optionsList.hide();
        plugin.isActive = false;
      }
    }
  };

  /**
   * Switch to the selected chapter using its resolved playback URL.
   *
   * @param event - The delegated option click event.
   * @param data - The previous and selected option values.
   */
  onItemClick = (event: DelegateEvent, data: ChangeData) => {
    super.onItemClick(event, data);

    let { url } = data.to;
    if (typeof url === 'string') {
      // resolve chapter URLs through the active playback mode
      const resolvedUrl: string = this.player.config.settings.resolveChapterUrl(url);
      if (isTranscodedStream(resolvedUrl)) {
        this.player.config.settings.showSwitchLoading();
      }
      url = resolvedUrl;
    }

    const { id, showText } = data.to;
    if (typeof this.config.chapterChange === 'function') {
      // let callers own navigation when a chapter callback is configured
      this.config.chapterChange({
        id: id,
        url: url,
        title: showText
      });
    } else if (typeof url === 'string') {
      this.playNext(url, showText);
    }
  };

  /**
   * Restart playback with metadata for the selected chapter.
   *
   * @param url - The resolved chapter URL.
   * @param title - The chapter title shown in the top bar.
   */
  private async playNext(url: string, title: string | number | undefined) {
    const { duration, progressDot } = await this.player.config.settings.probeMedia(url);
    this.player.getPlugin('progresspreview')?.updateAllDots(progressDot);
    this.player.playNext({ url, topBar: { title }, customDuration: duration });
  }

  /**
   * Register the app chapter icon.
   */
  registerIcons() {
    return {
      chapters: {
        icon: iconToSVG(icons.listCheck),
        class: 'size-6 text-white/80'
      }
    };
  }

  /**
   * Initialize and render the chapter options.
   */
  afterCreate() {
    super.afterCreate();
    this.renderItemList();
  }

  /**
   * Render chapter options and select the active chapter.
   */
  renderItemList() {
    const { config, optionsList, player } = this;

    this.curIndex = -1;
    const items = (config.list as Chapter[]).map((item, index) => {
      let url = player.config.url;
      if (typeof url === 'string' && isTranscodedStream(url)) {
        // compare transcoded playback against its direct chapter URL
        url = url.replace('&transcode=true', '');
      }
      const chapterItem = {
        id: item.id || '',
        url: item.url || '',
        showText: item.title,
        selected: (item.id || item.url) === (config.chapterId || url || '')
      };
      if (chapterItem.selected) {
        this.curIndex = index;
      }
      return chapterItem;
    });

    if (optionsList) {
      optionsList.renderItemList(items);
    } else {
      // side lists need an option list rooted in the player container
      const isSide = config.listType === 'side';
      this.optionsList = new OptionList({
        root: isSide ? player.innerContainer || player.root : this.root,
        config: {
          data: items || [],
          className: isSide ? 'xg-right-side xg-side-list xgplayer-chapters' : '',
          domEventType: 'click',
          onItemClick: this.onItemClick
        }
      });
      this.show();
    }
  }

  /**
   * Show the selector when chapters are available.
   */
  show() {
    if (this.config.list && this.config.list.length > 0) {
      super.show();
    }
  }

  /**
   * Destroy the chapter selector.
   */
  destroy() {
    super.destroy();
  }
}
