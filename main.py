import os
import json
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.ad import Ad

load_dotenv()
ACCESS_TOKEN = os.getenv('META_ACCESS_TOKEN')
AD_ACCOUNT_ID = os.getenv('META_AD_ACCOUNT_ID')

FacebookAdsApi.init(access_token=ACCESS_TOKEN)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🌟 新增：當任何人連上你的網址，直接把網頁檔案 (index.html) 給他看
@app.get("/")
def serve_webpage():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"error": "找不到 index.html 檔案，請確認檔案名稱與位置！"}

@app.get("/api/ads")
def get_ads_data(start_date: str = None, end_date: str = None, account_id: str = None):
    target_account_id = account_id if account_id else AD_ACCOUNT_ID
    account = AdAccount(target_account_id)
    
    fields = [
        'ad_id', 'ad_name', 'adset_name', 'objective', 
        'spend', 'impressions', 'reach', 'clicks', 'outbound_clicks', 
        'actions', 'action_values'
    ]
    
    if start_date and end_date:
        params = {'time_range': json.dumps({'since': start_date, 'until': end_date}), 'level': 'ad'}
    else:
        params = {'date_preset': 'last_7d', 'level': 'ad'}
    
    try:
        insights = account.get_insights(fields=fields, params=params)
        data_list = []
        ad_info_cache = {}  
        
        if insights:
            for item in insights:
                spend = float(item.get('spend', 0))
                if spend == 0:
                    continue

                ad_id = item.get('ad_id')
                audience_type = item.get('adset_name', '未標示受眾')
                
                # 抓取素材圖與隱藏的 Optimization Goal
                if ad_id in ad_info_cache:
                    image_url = ad_info_cache[ad_id]['image']
                    opt_goal = ad_info_cache[ad_id]['opt_goal']
                else:
                    image_url = ""
                    opt_goal = ""
                    if ad_id:
                        try:
                            ad = Ad(ad_id)
                            ad_details = ad.api_get(fields=['adset{optimization_goal}'])
                            if 'adset' in ad_details and 'optimization_goal' in ad_details['adset']:
                                opt_goal = ad_details['adset']['optimization_goal']

                            creatives = ad.get_ad_creatives(fields=[
                                'thumbnail_url', 'image_url', 'object_story_spec',
                                'effective_object_story_spec', 'asset_feed_spec'
                            ])
                            if creatives:
                                creative = creatives[0]
                                image_url = creative.get('thumbnail_url') or creative.get('image_url', '')
                                if not image_url:
                                    story = creative.get('effective_object_story_spec') or creative.get('object_story_spec', {})
                                    if 'link_data' in story and 'picture' in story['link_data']:
                                        image_url = story['link_data']['picture']
                                    elif 'video_data' in story and 'image_url' in story['video_data']:
                                        image_url = story['video_data']['image_url']
                                    elif 'link_data' in story and 'child_attachments' in story['link_data']:
                                        attachments = story['link_data']['child_attachments']
                                        if len(attachments) > 0 and 'picture' in attachments[0]:
                                            image_url = attachments[0]['picture']
                                if not image_url:
                                    asset = creative.get('asset_feed_spec', {})
                                    if 'images' in asset and len(asset['images']) > 0:
                                        image_url = asset['images'][0].get('url', '')
                                    elif 'videos' in asset and len(asset['videos']) > 0:
                                        image_url = asset['videos'][0].get('thumbnail_url', '')
                        except Exception as e:
                            pass 
                    ad_info_cache[ad_id] = {'image': image_url, 'opt_goal': opt_goal}

                def get_exact_action_value(actions_list, preferred_types):
                    if not actions_list: return 0.0
                    for p_type in preferred_types:
                        for act in actions_list:
                            if act.get('action_type') == p_type:
                                return float(act.get('value', 0))
                    return 0.0

                purchases = int(get_exact_action_value(item.get('actions', []), ['purchase', 'omni_purchase']))
                carts = int(get_exact_action_value(item.get('actions', []), ['add_to_cart', 'omni_add_to_cart']))
                conv_value = get_exact_action_value(item.get('action_values', []), ['purchase', 'omni_purchase'])
                leads = int(get_exact_action_value(item.get('actions', []), ['leadgen']))
                
                msg_starts = get_exact_action_value(item.get('actions', []), [
                    'onsite_conversion.messaging_conversation_started_7d', 
                    'messaging_conversation_started_7d',
                    'onsite_conversion.messaging_first_reply'
                ])
                comments = get_exact_action_value(item.get('actions', []), ['comment'])
                messages = int(msg_starts + comments)

                data_list.append({
                    "adName": item.get('ad_name', '未命名廣告'),
                    "adsetName": audience_type,
                    "objective": item.get('objective', ''),
                    "optimizationGoal": opt_goal,
                    "spend": spend,
                    "impressions": int(item.get('impressions', 0)),
                    "reach": int(item.get('reach', 0)),
                    "allClicks": int(item.get('clicks', 0)),
                    "purchases": purchases,
                    "convValue": conv_value,
                    "carts": carts,
                    "leads": leads,
                    "messages": messages,
                    "image": image_url
                })
        return {"status": "success", "data": data_list}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# 🌟 新增：讓雲端平台自動分配 Port 來啟動伺服器
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
