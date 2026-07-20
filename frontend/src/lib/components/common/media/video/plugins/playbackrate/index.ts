import { Events } from 'xgplayer';
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
 * The previous and selected playback rate option values.
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
 * A localized playback rate option.
 */
type RateItem = {
  rate: number;
  text: string;
  iconText?: string;
};

/**
 * Playback rate selector synchronized with player rate changes.
 */
export default class PlaybackRate extends OptionsIcon {
  /**
   * The xgplayer plugin name.
   */
  static get pluginName() {
    return 'playbackRate';
  }

  /**
   * The default playback rate selector configuration.
   */
  static get defaultConfig() {
    return {
      ...OptionsIcon.defaultConfig,
      className: 'xgplayer-playbackrate',
      isShowIcon: true,
      heightLimit: false,
      hidePortrait: false
    };
  }

  /**
   * Close other option menus before opening the rate selector.
   */
  onIconClick = () => {
    for (const name of ['definitions', 'chapters']) {
      const plugin = this.player.getPlugin(name);
      if (plugin && plugin.optionsList && plugin.isActive) {
        plugin.optionsList.hide();
        plugin.isActive = false;
      }
    }
  };

  /**
   * Apply the selected playback rate.
   *
   * @param event - The delegated option click event.
   * @param data - The previous and selected option values.
   */
  onItemClick = (event: DelegateEvent, data: ChangeData) => {
    super.onItemClick(event, data);
    const rate = Number(data.to.rate);
    if (rate && this.curValue !== rate) {
      this.curValue = rate;
      this.player.playbackRate = rate;
    }
  };

  /**
   * Initialize options and synchronize external rate changes.
   */
  afterCreate() {
    super.afterCreate();
    this.renderItemList();
    this.on(Events.RATE_CHANGE, () => {
      // reflect keyboard shortcuts and restored playback state
      if (this.curValue !== this.player.playbackRate) {
        this.renderItemList();
      }
    });
    this.bind('click', this.onIconClick);
  }

  /**
   * Render playback rate options and select the active rate.
   */
  renderItemList() {
    this.curIndex = -1;
    this.curValue = this.player.playbackRate || 1;
    const items = (this.config.list as RateItem[]).map((item, index) => {
      const rateItem = {
        rate: item.rate,
        showText: this.getTextByLang(item, 'text', null),
        selected: this.curValue === item.rate
      };
      if (rateItem.selected) {
        this.curIndex = index;
      }
      return rateItem;
    });
    super.renderItemList(items, this.curIndex);
  }

  /**
   * Update the control label for the active playback rate.
   */
  changeCurrentText() {
    if (this.isIcons) {
      return;
    }
    const iconText = this.find('.icon-text');
    if (iconText) {
      const rates: RateItem[] = this.config.list;
      const rate = rates[this.curIndex < rates.length ? this.curIndex : 0];
      if (rate) {
        iconText.innerHTML = this.getTextByLang(rate, 'iconText', null);
      } else {
        iconText.innerHTML = `${this.player.playbackRate.toFixed(1)}x`;
      }
    }
  }

  /**
   * Show the selector when playback rates are available.
   */
  show() {
    if (this.config.list && this.config.list.length > 0) {
      super.show();
    }
  }

  /**
   * Remove the playback rate selector event binding.
   */
  destroy() {
    super.destroy();
    this.unbind('click', this.onIconClick);
  }
}
