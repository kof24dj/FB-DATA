import os
import json
import time 
import uvicorn
import requests # 🌟 新增：用於發送批次查詢
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adreportrun import AdReportRun 

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

    fields_daily = [
        'ad_id', 'objective', 'spend', 'impressions', 'clicks', 'actions', 'action_values'
    ]
    
    params_std = {'level': 'ad'}
    params_reg = {'level': 'ad', 'breakdowns': ['region']}
    params_daily = {'level': 'ad', 'time_increment': 1} 
    
    if start_date and end_date:
        time_range = json.dumps({'since': start_date, 'until': end_date})
        params_std['time_range'] = time_range
        params_reg['time_range'] = time_range
        params_daily['time_range'] = time_range
    else:
        params_std['date_preset'] = 'last_7d'
        params_reg['date_preset'] = 'last_7d'
        params_daily['date_preset'] = 'last_7d'
    
    try:
        job_std = account.get_insights(fields=fields, params=params_std, is_async=True)
        job_reg = account.get_insights(fields=fields, params=params_reg, is_async=True)
        job_daily = account.get_insights(fields=fields_daily, params=params_daily, is_async=True)
        
        while True:
            job_std.api_get()
            job_reg.api_get()
            job_daily.api_get()
            
            status_std = job_std.get(AdReportRun.Field.async_status)
            status_reg = job_reg.get(AdReportRun.Field.async_status)
            status_daily = job_daily.get(AdReportRun.Field.async_status)
            
            if "Job Failed" in [status_std, status_reg, status_daily]:
                return {"status": "error", "message": "Meta 伺服器處理報表失敗，請稍後再試。"}
                
            if status_std == "Job Completed" and status_reg == "Job Completed" and status_daily == "Job Completed":
                break
            time.sleep(2) 
            
# (前方維持不變...)
            if status_std == "Job Completed" and status_reg == "Job Completed" and status_daily == "Job Completed":
                break
            time.sleep(2) 
            
        # 🌟 修正：直接將 Cursor 轉換並儲存為 List，避免資料被消耗掉
        std_list = list(job_std.get_result()) if job_std.get_result() else []
        reg_list = list(job_reg.get_result()) if job_reg.get_result() else []
        daily_list_raw = list(job_daily.get_result()) if job_daily.get_result() else []
        
        # 🌟 蒐集所有不重複的 ad_id，一次性批次索取圖片與優化目標
        all_items = std_list + reg_list + daily_list_raw
        unique_ad_ids = list({item.get('ad_id') for item in all_items if item.get('ad_id')})
        
        ad_info_cache = {}
        chunk_size = 50 # FB API 限制每次最多查 50 筆 ID
        
        for i in range(0, len(unique_ad_ids), chunk_size):
            chunk = unique_ad_ids[i:i+chunk_size]
            try:
                url = "https://graph.facebook.com/v18.0/"
                params = {
                    'ids': ','.join(chunk),
                    'fields': 'adset{optimization_goal},adcreatives{thumbnail_url,image_url,object_story_spec,effective_object_story_spec,asset_feed_spec}',
                    'access_token': ACCESS_TOKEN
                }
                res = requests.get(url, params=params)
                res_data = res.json()
                
                for ad_id, ad_data in res_data.items():
                    opt_goal = ""
                    if 'adset' in ad_data and 'optimization_goal' in ad_data['adset']:
                        opt_goal = ad_data['adset']['optimization_goal']
                        
                    image_url = ""
                    if 'adcreatives' in ad_data and 'data' in ad_data['adcreatives']:
                        creatives = ad_data['adcreatives']['data']
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
                    
                    ad_info_cache[ad_id] = {'image': image_url, 'opt_goal': opt_goal}
            except Exception as e:
                print(f"Error fetching bulk ad info: {e}")

        def get_exact_action_value(actions_list, preferred_types):
            if not actions_list: return 0.0
            for p_type in preferred_types:
                for act in actions_list:
                    if act.get('action_type') == p_type:
                        return float(act.get('value', 0))
            return 0.0

        def parse_insight_item(item):
            spend = float(item.get('spend', 0))
            if spend == 0: return None

            ad_id = item.get('ad_id')
            audience_type = item.get('adset_name', '未標示受眾')
            region_name = item.get('region', '未知區域') 
            
            image_url = ad_info_cache.get(ad_id, {}).get('image', '')
            opt_goal = ad_info_cache.get(ad_id, {}).get('opt_goal', '')

            purchases = int(get_exact_action_value(item.get('actions', []), ['purchase', 'omni_purchase', 'offsite_conversion.fb_pixel_purchase']))
            carts = int(get_exact_action_value(item.get('actions', []), ['add_to_cart', 'omni_add_to_cart', 'offsite_conversion.fb_pixel_add_to_cart']))
            conv_value = get_exact_action_value(item.get('action_values', []), ['purchase', 'omni_purchase', 'offsite_conversion.fb_pixel_purchase'])
            leads = int(get_exact_action_value(item.get('actions', []), ['lead', 'onsite_conversion.lead_grouped']))
            
            msg_starts = get_exact_action_value(item.get('actions', []), [
                'onsite_conversion.messaging_conversation_started_7d', 
                'messaging_conversation_started_7d',
                'onsite_conversion.messaging_first_reply'
            ])
            comments = get_exact_action_value(item.get('actions', []), ['comment'])
            messages = int(msg_starts + comments)

            return {
                "adName": item.get('ad_name', '未命名廣告'),
                "adsetName": audience_type,
                "region": region_name, 
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
            }

        def parse_daily_item(item):
            spend = float(item.get('spend', 0))
            if spend == 0: return None
            
            ad_id = item.get('ad_id')
            opt_goal = ad_info_cache.get(ad_id, {}).get('opt_goal', '')
            
            purchases = int(get_exact_action_value(item.get('actions', []), ['purchase', 'omni_purchase', 'offsite_conversion.fb_pixel_purchase']))
            conv_value = get_exact_action_value(item.get('action_values', []), ['purchase', 'omni_purchase', 'offsite_conversion.fb_pixel_purchase'])
            leads = int(get_exact_action_value(item.get('actions', []), ['lead', 'onsite_conversion.lead_grouped']))
            
            msg_starts = get_exact_action_value(item.get('actions', []), [
                'onsite_conversion.messaging_conversation_started_7d', 
                'messaging_conversation_started_7d',
                'onsite_conversion.messaging_first_reply'
            ])
            comments = get_exact_action_value(item.get('actions', []), ['comment'])
            
            return {
                "date": item.get('date_start', ''),
                "objective": item.get('objective', ''),
                "optimizationGoal": opt_goal,
                "spend": spend,
                "impressions": int(item.get('impressions', 0)),
                "clicks": int(item.get('clicks', 0)),
                "purchases": purchases,
                "convValue": conv_value,
                "leads": leads,
                "messages": int(msg_starts + comments)
            }

        data_list = []
        for item in std_list:
            parsed = parse_insight_item(item)
            if parsed: data_list.append(parsed)
                
        region_list = []
        for item in reg_list:
            parsed = parse_insight_item(item)
            if parsed: region_list.append(parsed)

        daily_list = []
        for item in daily_list_raw:
            parsed = parse_daily_item(item)
            if parsed: daily_list.append(parsed)

        return {"status": "success", "data": data_list, "region_data": region_list, "daily_data": daily_list}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
