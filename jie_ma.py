#!/usr/bin/env python3
import json
import requests
from aliyunsdkcore.client import AcsClient
from aliyunsdkalidns.request.v20150109.DescribeDomainRecordsRequest import DescribeDomainRecordsRequest
from aliyunsdkalidns.request.v20150109.UpdateDomainRecordRequest import UpdateDomainRecordRequest

# ========== 必填配置 ==========
ACCESS_KEY_ID     = '阿里云ID'
ACCESS_KEY_SECRET = '阿里云密钥'
DOMAIN            = '域名'
SUB_DOMAIN        = ''                      # 根域名
# =================================

client = AcsClient(ACCESS_KEY_ID, ACCESS_KEY_SECRET, 'cn-hangzhou')

def get_current_ip(ip_type='A'):
    try:
        url = 'https://v4.ident.me' if ip_type == 'A' else 'https://v6.ident.me'
        return requests.get(url, timeout=5).text.strip()
    except Exception as e:
        print(f"[{ip_type}] 获取IP失败: {e}")
        return None

def get_record_id(domain, sub_domain, ip_type='A'):
    req = DescribeDomainRecordsRequest()
    req.set_DomainName(domain)
    req.set_TypeKeyWord(ip_type)
    req.set_RRKeyWord('')          # 查根域名
    resp = client.do_action_with_exception(req)
    data = json.loads(resp)
    print(f"[调试] 查询 {ip_type} 记录返回：")
    for record in data['DomainRecords']['Record']:
        print("  RR:", repr(record['RR']), "Value:", record['Value'])
    for record in data['DomainRecords']['Record']:

        if record['RR'] == '@':
            return record['RecordId'], record['Value']
    return None, None


def update_record(record_id, rr, ip_type, value):
    req = UpdateDomainRecordRequest()
    req.set_RecordId(record_id)
    req.set_RR(rr)                 # 根域名传 ''
    req.set_Type(ip_type)
    req.set_Value(value)
    client.do_action_with_exception(req)

def main():
    for ip_type in ['A', 'AAAA']:
        current_ip = get_current_ip(ip_type)
        if not current_ip:
            print(f"[{ip_type}] 获取IP失败，跳过")
            continue

        record_id, old_ip = get_record_id(DOMAIN, SUB_DOMAIN, ip_type)
        if not record_id:
            print(f"[{ip_type}] 未找到解析记录，跳过")
            continue

        if old_ip == current_ip:
            print(f"[{ip_type}] IP未变化，跳过")
            continue

        update_record(record_id, '@', ip_type, current_ip)
        print(f"[{ip_type}] 更新成功: {old_ip} → {current_ip}")

if __name__ == '__main__':
    main()